#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_watch.py — сборщик мирового интеллекта по Jev (TypeSafe AI "System One").

Зачем: официальный сайт typesafe.ai и docs.typesafe.ai из России недоступны
(гео-блок), поэтому русские каналы перепечатывают один и тот же вторичный текст.
Мы собираем первичные мировые источники сами.

Источники (все ходят напрямую, без прокси — проверено, 0.7-2с):
  1. GitHub Search API      — репозитории по jev (нужен GITHUB_TOKEN, 30/мин)
  2. robokrunch/awesome-jev — кураторский список, коммиты через Atom
  3. Hacker News (Algolia)  — обсуждения и Show HN
  4. HuggingFace API        — модели/чекпойнты
  5. Bing News RSS          — новости на 6 рынках, включая ru-RU

Результат: ~/channels/jev_intel.json  (отдельное хранилище, НЕ queue.json)
Перевод русской аннотации — через локальный GLM-мост (--translate).

ВАЖНО: скрипт ничего не публикует и не пишет в queue.json. Он только копит
сырьё. Дальше — отбор и подготовка постов вручную.

Запуск:
  python3 jev_watch.py              # собрать и показать сводку
  python3 jev_watch.py --translate  # плюс русские аннотации через GLM
  python3 jev_watch.py --list       # показать накопленное
  python3 jev_watch.py --since 24h  # только за последние сутки
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from conveyor import fetch  # noqa: E402

STORE = os.path.join(HERE, "jev_intel.json")
BRIDGE = "http://127.0.0.1:3001/v1/chat/completions"
BRIDGE_TOKEN = "glm-local"
AWESOME_REPO = "robokrunch/awesome-jev"

# GitHub Search: 30 запросов/мин с токеном. Держим запас для других потребителей.
GH_QUERIES = ["typesafe jev", "jev classifier", "jev 1.13", "jev decisions"]
GH_MIN_INTERVAL = 90  # секунд между прогонами, иначе упираемся в лимит
HN_QUERIES = ["typesafe jev", "jev 1.13"]
BING_MARKETS = ["ru-RU", "en-US", "zh-CN", "ko-KR", "es-ES", "de-DE"]
BING_QUERY = "Jev AI TypeSafe"
HF_QUERIES = ["typesafe", "jev-1.13", "system-one"]

# Что считаем релевантным Jev (иначе «jev» ловит мусор)
RELEVANT = re.compile(r"typesafe|jev[\s\-_]?1\.13|system\s*one|decision model|jev\b", re.I)
NEGATIVE = re.compile(r"jevt?e|\bjeva\b|abreu|baros|alvar|jov\b", re.I)


def load_dotenv():
    try:
        with open(os.path.join(HERE, ".env"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def norm_url(u):
    """Канонический вид URL для дедупа: без utm/трекинга и query."""
    if not u:
        return ""
    p = urllib.parse.urlsplit(u.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm") and k.lower() not in ("ref", "source", "fbclid", "gclid")]
    host = p.netloc.lower().replace("www.", "")
    return urllib.parse.urlunsplit((p.scheme.lower(), host, p.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def is_relevant(text):
    t = text or ""
    if NEGATIVE.search(t):
        return False
    return bool(RELEVANT.search(t))


def gh_headers(tok):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "channels-jev-watch",
         "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def gh_json(url, tok, tmo=30):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers=gh_headers(tok))
    with op.open(req, timeout=tmo) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


# ── источники ────────────────────────────────────────────────────────────────
def src_github(tok, since):
    out = []
    for q in GH_QUERIES:
        try:
            u = "https://api.github.com/search/repositories?q=%s&sort=updated&order=desc&per_page=25" \
                % urllib.parse.quote(q)
            d = gh_json(u, tok)
            for it in d.get("items", []):
                pushed = (it.get("pushed_at") or "")[:10]
                if since and pushed and pushed < since:
                    continue
                desc = it.get("description") or ""
                out.append({
                    "src": "github", "key": "gh:" + it["full_name"],
                    "url": it["html_url"], "title": it["full_name"],
                    "text": desc, "stars": it.get("stargazers_count", 0),
                    "created": (it.get("created_at") or "")[:10], "pushed": pushed,
                    "lang": it.get("language") or "",
                })
        except Exception as e:
            out.append({"src": "github", "key": "err:gh:%s" % q, "url": "",
                        "title": "GitHub ошибка", "text": "%s: %s" % (q, str(e)[:120])})
        time.sleep(1.2)
    return out


def src_awesome(since):
    """Коммиты кураторского списка — там появляются новые ссылки на Jev."""
    out = []
    try:
        raw = fetch("https://github.com/%s/commits/main.atom" % AWESOME_REPO, None, timeout=30)
        root = ET.fromstring(raw.encode("utf-8"))
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in root.findall("a:entry", ns):
            title = (e.findtext("a:title", "", ns) or "").strip()
            upd = (e.findtext("a:updated", "", ns) or "")[:10]
            link = ""
            for l in e.findall("a:link", ns):
                if l.get("rel") == "alternate":
                    link = l.get("href") or ""
                    break
            if since and upd and upd < since:
                continue
            out.append({"src": "awesome", "key": "aw:%s:%s" % (AWESOME_REPO, norm_url(link) or title),
                        "url": link, "title": "awesome-jev: %s" % title, "text": "", "pushed": upd})
    except Exception as e:
        out.append({"src": "awesome", "key": "err:aw", "url": "",
                    "title": "awesome-jev ошибка", "text": str(e)[:150]})
    return out


def src_hn(since):
    out = []
    cutoff = since
    for q in HN_QUERIES:
        try:
            u = ("https://hn.algolia.com/api/v1/search_by_date?query=%s&tags=story&hitsPerPage=40"
                 % urllib.parse.quote(q))
            d = json.loads(fetch(u, None, timeout=30))
            for h in d.get("hits", []):
                cid = h.get("created_at", "")[:10]
                if cutoff and cid and cid < cutoff:
                    continue
                title = h.get("title") or ""
                url = h.get("url") or ""
                if not is_relevant(title + " " + url):
                    continue
                out.append({"src": "hn", "key": "hn:%s" % (h.get("objectID")),
                            "url": norm_url(url) or "https://news.ycombinator.com/item?id=%s" % h.get("objectID"),
                            "title": title, "text": (h.get("story_text") or "")[:600],
                            "points": h.get("points") or 0, "comments": h.get("num_comments") or 0,
                            "pushed": cid})
        except Exception as e:
            out.append({"src": "hn", "key": "err:hn:%s" % q, "url": "",
                        "title": "HN ошибка", "text": str(e)[:150]})
        time.sleep(0.8)
    return out


def src_hf(since):
    out = []
    for q in HF_QUERIES:
        try:
            d = json.loads(fetch("https://huggingface.co/api/models?search=%s&limit=20&sort=lastModified"
                                 % urllib.parse.quote(q), None, timeout=25))
            for m in d if isinstance(d, list) else []:
                mid = m.get("modelId") or ""
                mod = (m.get("lastModified") or "")[:10]
                if since and mod and mod < since:
                    continue
                if not is_relevant(mid + " " + " ".join(m.get("tags") or [])):
                    continue
                out.append({"src": "huggingface", "key": "hf:" + mid,
                            "url": "https://huggingface.co/%s" % mid, "title": mid,
                            "text": "tags: %s" % ", ".join((m.get("tags") or [])[:8]),
                            "stars": m.get("likes") or 0, "pushed": mod})
        except Exception as e:
            out.append({"src": "huggingface", "key": "err:hf:%s" % q, "url": "",
                        "title": "HF ошибка", "text": str(e)[:150]})
        time.sleep(0.5)
    return out


def src_bing(since):
    out = []
    for mkt in BING_MARKETS:
        try:
            u = ("https://www.bing.com/news/search?q=%s&format=RSS&mkt=%s&setmkt=%s"
                 % (urllib.parse.quote(BING_QUERY), mkt, mkt))
            root = ET.fromstring(fetch(u, None, timeout=25).encode("utf-8"))
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if not is_relevant(title):
                    continue
                pd = (it.findtext("pubDate") or "")[:16]
                out.append({"src": "bing:" + mkt, "key": "bing:" + norm_url(link),
                            "url": link, "title": title,
                            "text": re.sub(r"<[^>]+>", " ", it.findtext("description") or "").strip()[:400],
                            "pushed": pd})
        except Exception as e:
            out.append({"src": "bing:" + mkt, "key": "err:bing:%s" % mkt, "url": "",
                        "title": "Bing %s ошибка" % mkt, "text": str(e)[:120]})
        time.sleep(0.6)
    return out


# ── перевод через локальный GLM ──────────────────────────────────────────────
GLM_SYS = ("Ты — переводчик и редактор. Тебе дают материал о модели Jev (TypeSafe AI System One). "
           "Напиши по-русски ОДИН абзац на 3-5 предложений: что это, ключевая цифра или факт, "
           "почему это важно. Без воды, без оценок, без призыва к действию. "
           "Сохрани числа и названия как есть. Если в материале есть конкретные измерения "
           "(latency, цена, точность, звёзды) — обязательно включи их.")


def bridge(system, user, timeout=90, max_tokens=400):
    payload = {"model": "glm-5.3", "temperature": 0.2, "max_tokens": max_tokens, "stream": False,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(BRIDGE, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + BRIDGE_TOKEN,
                                          "Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        return (json.loads(r.read().decode("utf-8", "ignore")).get("choices") or [{}])[0] \
            .get("message", {}).get("content", "") or ""


# ── основной цикл ────────────────────────────────────────────────────────────
def collect(since, tok):
    items = []
    for fn in (lambda: src_github(tok, since), lambda: src_awesome(since),
               lambda: src_hn(since), lambda: src_hf(since), lambda: src_bing(since)):
        try:
            items.extend(fn())
        except Exception as e:
            items.append({"src": "fatal", "key": "err:fatal", "url": "", "title": "сбой",
                          "text": str(e)[:150]})
    return items


def main():
    args = sys.argv[1:]
    load_dotenv()
    tok = os.environ.get("GITHUB_TOKEN", "")

    if "--list" in args:
        st = load(STORE)
        its = st.get("items", [])
        print("\n════ накоплено: %d материалов ════" % len(its))
        by = {}
        for it in its:
            by.setdefault(it.get("src", "?"), []).append(it)
        for s, v in sorted(by.items(), key=lambda x: -len(x[1])):
            print("\n── %s: %d ──" % (s, len(v)))
            for it in v[:40]:
                print("  %-11s %s" % (it.get("pushed") or "----------", (it.get("title") or "")[:74]))
        return

    since = None
    if "--since" in args:
        i = args.index("--since")
        val = args[i + 1] if i + 1 < len(args) else "24h"
        m = re.match(r"^(\d+)([hd])$", val)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            days = n if unit == "d" else n / 24.0
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    if "--all" in args:
        since = "2000-01-01"

    st = load(STORE)
    seen = st.get("seen", {})
    items = st.get("items", [])
    last_gh = st.get("last_github_run")
    do_gh = True
    if last_gh:
        try:
            dt = datetime.fromisoformat(last_gh)
            if (datetime.now(timezone.utc) - dt).total_seconds() < GH_MIN_INTERVAL:
                do_gh = False
        except Exception:
            pass

    print("════ jev_watch ════")
    print("  режим: %s" % ("всё" if since in (None, "2000-01-01") else "с %s" % since))
    print("  GitHub: %s (лимит %d/мин, токен %s)"
          % ("да" if do_gh else "пропуск, недавно бежали", 30, "есть" if tok else "НЕТ"))
    print("  хранилище: %s (%d материалов, %d ключей)" % (STORE, len(items), len(seen)))

    fresh = []
    t0 = time.time()
    for fn_name, fn in (("github", lambda: src_github(tok, since)), ("awesome", lambda: src_awesome(since)),
                        ("hn", lambda: src_hn(since)), ("hf", lambda: src_hf(since)),
                        ("bing", lambda: src_bing(since))):
        if fn_name == "github" and not do_gh:
            continue
        t = time.time()
        got = fn()
        new = [g for g in got if g["key"] not in seen]
        fresh.extend(new)
        print("  %-8s получено %3d, новых %3d  (%.1fs)" % (fn_name, len(got), len(new), time.time() - t))
        time.sleep(0.4)

    if "--translate" in args and fresh:
        print("\n  перевод %d новых через GLM…" % len(fresh))
        for i, it in enumerate(fresh, 1):
            body = "%s\n\n%s" % (it.get("title", ""), (it.get("text") or "")[:800])
            try:
                it["ru"] = bridge(GLM_SYS, body).strip()
            except Exception as e:
                it["ru"] = "(перевод не удался: %s)" % str(e)[:60]
            print("    %2d/%d %s" % (i, len(fresh), (it.get("title") or "")[:56]))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for it in fresh:
        it["first_seen"] = now
        seen[it["key"]] = {"src": it.get("src"), "ts": now}
        items.append(it)
    if len(seen) > 5000:
        for k in list(seen)[:len(seen) - 5000]:
            seen.pop(k, None)
    st.update({"seen": seen, "items": items, "last_run": now,
               "last_github_run": now if do_gh else st.get("last_github_run")})
    json.dump(st, open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n  ИТОГ: новых %d, всего %d, за %.1fs → %s" % (len(fresh), len(items), time.time() - t0, STORE))
    if fresh:
        print("\n──── новые (топ-20) ────")
        fresh.sort(key=lambda x: -(x.get("stars") or x.get("points") or 0))
        for it in fresh[:20]:
            if it["key"].startswith("err:"):
                continue
            print("  [%-11s] %s" % (it.get("src"), (it.get("title") or "")[:66]))
            if it.get("text"):
                print("        %s" % it["text"][:100].strip())
            if it.get("ru"):
                print("   RU:  %s" % it["ru"][:150])


if __name__ == "__main__":
    main()
