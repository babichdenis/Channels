# -*- coding: utf-8 -*-
"""linkcheck — реальная проверка, куда ведёт каждая ссылка поста.

Зачем: домен в тексте мало что значит. Реальный признак рекламы —
это то, что находится ПОСЛЕ редиректа, плюс метаданные посадочной
страницы. Здесь мы идём по цепочке редиректов, снимаем title /
og:site_name / description и ищем в них рекламные маркеры.

Все результаты кэшируются в ad_link_cache.json, чтобы не долбить
сеть на каждом посте. Есть защита от SSRF: приватные адреса, localhost
и нестандартные порты не обходятся.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlparse, parse_qs

import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "ad_link_cache.json")

TIMEOUT = 7.0
MAX_REDIRECTS = 6
MAX_BODY = 200_000
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ── куда не ходим никогда ────────────────────────────────────────────────
ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = {80, 443, 8080, 8443}

# ── маркеры рекламной посадочной ─────────────────────────────────────────
_AD_TITLE = re.compile(
    r"(?i)(?:ai\s*генератор|нейросет|подписк|тариф|цена|pricing|plans?|"
    r"промокод|промо|скидк|распродаж|black\s*friday|bonus|credits?|"
    r"top\s*\d+|лучшие\s+сервис|каталог\s+моделей|все\s+модели|"
    r"доступ\s+к\s+всем|без\s+лимит|aggregator|api\s+keys?|"
    r"попробовать\s+бесплатно|начать\s+работу|start\s+free|"
    r"pricing|per\s+million\s+tokens|sign\s*up|log\s*in)")
_AD_BODY = re.compile(
    r"(?i)(?:t\.me/|telegram\.me/|joinchat|t\.me/\+|"
    r"при\s+подписк\w*|цена\s*от|от\s+\d+\s*₽|₽\s*/\s*(?:мес|month)|"
    r"per\s+million\s+tokens|input:\s*rp|output:\s*rp|"
    r"промокод|скидк\w*\s*\d+\s*%|бонусн\w*\s+кредит|"
    r"referral|реферальн\w*|приведи\s+друга|"
    r"limited\s+time|free\s+trial|trial\s+ends|"
    r"подписк\w*|тарифн\w*\s+план\w*)")
_SUSPICIOUS_TLD = ("top", "icu", "buzz", "click", "link", "lol", "monster",
                   "quest", "rest", "gq", "cfd", "sbs", "icu")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


# ── кэш ──────────────────────────────────────────────────────────────────
def load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    tmp = CACHE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def _is_public(host: str) -> bool:
    """Отсекаем SSRF: приватные сети, loopback, link-local."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return False
    return True


def _fetch(url: str, method: str):
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    })
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_CTX),
        _NoRedirect(),
    )
    return opener.open(req, timeout=TIMEOUT)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Редиректы разбираем сами — нужен полный список шагов."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_limited(r) -> bytes:
    chunks, total = [], 0
    while total < MAX_BODY:
        chunk = r.read(16_384)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _meta(html: str) -> dict:
    def grab(pattern):
        m = re.search(pattern, html, re.I | re.S)
        return m.group(1).strip() if m else ""

    return {
        "title": grab(r"<title[^>]*>(.*?)</title>"),
        "og_site": grab(r"<meta[^>]+property=[\"']og:site_name[\"'][^>]+content=[\"']([^\"']+)"),
        "desc": grab(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)"),
    }


def _domain_of(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _score_signals(final_url: str, meta: dict, body: str,
                   chain: list) -> tuple:
    """Возвращает (score, флаги). Домен/редирект/посадочная."""
    flags, score = [], 0
    dom = _domain_of(final_url)
    parts = dom.split(".")
    tld = parts[-1] if len(parts) > 1 else ""

    if tld in _SUSPICIOUS_TLD:
        flags.append("suspicious_tld:%s" % tld)
        score += 30

    if len(chain) > 1:
        flags.append("redirect:%d" % len(chain))
        score += 10

    blob_title = " ".join([meta.get("title", ""), meta.get("og_site", "")])
    m = _AD_TITLE.search(blob_title)
    if m:
        flags.append("ad_title:%s" % m.group(0)[:24])
        score += 35

    m = _AD_BODY.search(body[:40_000]) if body else None
    if m:
        flags.append("ad_body:%s" % m.group(0)[:24])
        score += 30

    if "t.me/" in body[:40_000] or "telegram.me/" in body[:40_000]:
        flags.append("body_telegram_invite")
        score += 20

    return score, flags


def check(url: str, cache: dict | None = None,
          persist: bool = True) -> dict:
    """Разрешает один URL. Возвращает dict с final_url/доменом/флагами.

    deep=False — только быстрый HEAD (кеш по домену).
    deep=True  — полный GET с разбором посадочной.
    """
    cache = cache if cache is not None else load_cache()
    key = url.strip()
    if key in cache:
        hit = dict(cache[key])
        hit["cached"] = True
        return hit

    result = {
        "url": key, "final_url": None, "domain": None, "host": None,
        "chain": [], "status": None, "title": "", "og_site": "",
        "desc": "", "score": 0, "flags": [], "error": None, "cached": False,
    }
    dom = _domain_of(key if "://" in key else "https://" + key)
    result["domain"] = dom or None
    result["host"] = dom.split(".")[0] if dom else None

    if not dom:
        result["error"] = "no_domain"
        return result

    try:
        parsed = urlparse(key if "://" in key else "https://" + key)
    except Exception:
        result["error"] = "bad_url"
        return result

    if parsed.scheme not in ALLOWED_SCHEMES:
        result["error"] = "bad_scheme"
        return result
    if parsed.port and parsed.port not in ALLOWED_PORTS:
        result["error"] = "bad_port"
        return result
    if not _is_public(parsed.netloc.split(":")[0]):
        result["error"] = "ssrf_blocked"
        return result

    current = key if "://" in key else "https://" + key
    body, hops = "", 0
    try:
        for _ in range(MAX_REDIRECTS + 1):
            hops += 1
            try:
                resp = _fetch(current, "HEAD")
                result["status"] = getattr(resp, "status", None) or resp.getcode()
                result["final_url"] = current
                break
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location")
                    if not loc:
                        result["status"] = e.code
                        result["final_url"] = current
                        break
                    nxt = urljoin(current, loc)
                    result["chain"].append(nxt)
                    current = nxt
                    continue
                result["status"] = e.code
                result["final_url"] = current
                break
            except Exception as e:
                result["error"] = "head:%s" % type(e).__name__
                result["final_url"] = current
                break

        if result["final_url"] is None and result["chain"]:
            result["final_url"] = current

        # GET — нужны title и тело
        try:
            g = _fetch(current, "GET")
            raw = _read_limited(g)
            body = raw.decode("utf-8", "replace")
            result["status"] = result["status"] or getattr(g, "status", None)
            result["final_url"] = current
        except Exception:
            pass

    except Exception as e:
        result["error"] = result["error"] or type(e).__name__

    meta = _meta(body) if body else {"title": "", "og_site": "", "desc": ""}
    result["title"] = meta["title"][:200]
    result["og_site"] = meta["og_site"][:120]
    result["desc"] = meta["desc"][:300]

    if result["final_url"]:
        result["domain"] = _domain_of(result["final_url"])
        result["host"] = (result["domain"] or "").split(".")[0] or None

    result["score"], result["flags"] = _score_signals(
        result["final_url"] or current, meta, body, result["chain"])

    if persist and result["error"] in (None, "head:HTTPError"):
        cache[key] = {k: v for k, v in result.items() if k != "cached"}
        save_cache(cache)
    return result


def check_many(urls, cache=None, deep=True) -> dict:
    cache = cache if cache is not None else load_cache()
    out = {}
    for u in urls:
        try:
            out[u] = check(u, cache)
        except Exception as e:
            out[u] = {"url": u, "error": "exc:%s" % type(e).__name__,
                      "flags": [], "score": 0}
    return out


if __name__ == "__main__":
    import sys
    for u in sys.argv[1:]:
        r = check(u)
        print("%-42s -> %-34s st=%s score=%s %s" % (
            u[:42], (r["final_url"] or "-")[:34], r["status"],
            r["score"], ",".join(r["flags"])[:60]))
        if r.get("title"):
            print("    title: %s" % r["title"][:90])
