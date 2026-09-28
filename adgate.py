# -*- coding: utf-8 -*-
"""Рекламный гейт: решает, показывать ли пост человеку вообще.

Порядок работы — тот, что нужен:

1. РАЗОБРАТЬ ССЫЛКИ. Каждая ссылка проверяется отдельно: домен, query-параметры,
   реферальные метки, приватные инвайты, сокращатели, положение ссылки (внутри
   CTA-строки или в обычном тексте). Ссылка-источник (x.com автора в новости
   про его же бенчмарк) рекламой НЕ считается.
2. ПРОВЕРИТЬ ФРАЗЫ. Паттерны берутся из adfilter (проверены на 3000 постов)
   и дополняются недостающими.
3. СОБРАТЬ КРАСНЫЕ ФЛАГИ, почистить текст. Если реклама была, а после чистки
   ничего не осталось — пост выкидывается целиком (ad_only).

Без сети: только статический разбор. Глубокая проверка ссылок (редиректы,
заголовки) живёт в linkcheck.py и кладёт вердикты в ad_link_cache.json.
"""
import json
import os
import re
import time
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "ad_link_cache.json")
DOMAINS_FILE = os.path.join(HERE, "ad_domains.json")

# Сумма весов, при которой пост не показываем вообще.
DROP_SCORE = 100
# Реклама была, а осталось меньше этого — значит пост был только рекламой.
MIN_CHARS = 180
MIN_SENTS = 2
# Порог, с которого сигналы посадочной страницы считаются сильными.
LINK_SCORE_MIN = 35

# Бюджет времени на разбор одного поста. Цикл бота держит long polling
# getUpdates, поэтому упираться в сеть надолго нельзя: при исчерпании
# бюджета deep/LLM пропускаются, а пост разбирается в следующем цикле.
_deadline = [None]


def set_deadline(seconds):
    import time as _t
    _deadline[0] = (_t.monotonic() + seconds) if seconds and seconds > 0 else None


def _expired():
    return bool(_deadline[0]) and _dead_time_left() <= 0


def _dead_time_left():
    import time as _t
    return _deadline[0] - _t.monotonic()


# ── deny-лист рекламы ────────────────────────────────────────────────────
# queue.json пишут сразу несколько процессов (conveyor, repost, scheduler),
# и они затирают status/ad_gate у постов, которые бот уже отверг. Поэтому
# решение о drop живёт в отдельном файле, который никто не переписывает.
# Ключ — id поста И хэш текста: repost может пересоздать тот же пост с новым id.
DENY_PATH = os.path.join(HERE, "ad_deny.json")


def text_key(text):
    import hashlib
    return hashlib.sha1(strip_tags(text or "").strip().encode("utf-8")).hexdigest()[:16]


def load_deny():
    try:
        with open(DENY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def deny_post(post, reason="", score=0, ad_links=None, phrases=None):
    """Записывает пост в deny-лист. Вызывать сразу при решении drop."""
    deny = load_deny()
    pid = str(post.get("id") or post.get("post_id") or "")
    h = text_key(post.get("text") or "")
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "channel": post.get("channel") or post.get("source") or "",
        "reason": reason, "score": score,
        "ad_links": ad_links or [], "phrases": phrases or [],
    }
    deny[pid] = entry
    deny["h:" + h] = entry
    try:
        tmp = DENY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(deny, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DENY_PATH)
    except Exception as exc:
        log_glm("deny-лист не записан (%s): %s" % (type(exc).__name__, exc))
    return entry


def is_denied(post):
    """Пост уже отвергнут? Проверяем и по id, и по содержимому."""
    deny = load_deny()
    pid = str(post.get("id") or post.get("post_id") or "")
    if pid and pid in deny:
        return deny[pid]
    h = "h:" + text_key(post.get("text") or "")
    return deny.get(h)


# ── состояние GLM ────────────────────────────────────────────────────────
# Апстрим chat.z.ai режет IP через Aliyun WAF и отдаёт 503 waf_block.
# Без автомата каждый пост ждал бы по минуте ретраев и цикл бота вставал бы.
# При блокировке молча уходим на статику+deep и пишем в лог один раз.
GLM_TIMEOUT = float(os.environ.get("AD_GATE_LLM_TIMEOUT", "25"))
GLM_COOLDOWN = float(os.environ.get("AD_GATE_LLM_COOLDOWN", "900"))
_glm = {"fails": 0, "down_until": 0.0, "reason": ""}


def glm_available():
    return _glm["down_until"] <= time.time()


def _glm_state():
    return _glm


def _glm_ok():
    if _glm["fails"]:
        log_glm("GLM снова отвечает (%s)" % _glm["reason"] or "ok")
    _glm["fails"] = 0
    _glm["down_until"] = 0.0
    _glm["reason"] = ""


def _glm_failed(detail=""):
    _glm["fails"] += 1
    _glm["reason"] = str(detail)[:200]
    if _glm["fails"] >= GLM_FAIL_THRESHOLD:
        _glm["down_until"] = time.time() + GLM_COOLDOWN
        log_glm("GLM недоступен, пауза %s с. Причина: %s" % (
            int(GLM_COOLDOWN), _glm["reason"]))
        _glm["fails"] = 0


GLM_FAIL_THRESHOLD = int(os.environ.get("AD_GATE_LLM_FAILS", "2"))
_glm_logged = [False]


def log_glm(msg):
    if not _glm_logged[0]:
        _glm_logged[0] = True
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "adgate.log")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except Exception:
            pass

DEFAULT_AD_DOMAINS = [
    "study24.ai", "syntx.ai", "aigrator.ai", "lava.top",
    "experientiallabs.ai", "pirateface.co", "bynara.id",
]

# Домен, который сам по себе рекламой не является: ссылки на источники,
# документацию, официальные сайты вендоров.
TRUSTED_DOMAINS = {
    "github.com", "githubusercontent.com", "github.io", "gitlab.com",
    "habr.com", "habr.ru", "arxiv.org", "openai.com", "anthropic.com",
    "claude.ai", "x.ai", "huggingface.co", "the-decoder.com",
    "techcrunch.com", "reuters.com", "forbes.ru", "vc.ru", "youtube.com",
    "youtu.be", "docs.docker.com", "google.com", "notion.site",
    "wikipedia.org", "mdn.io", "t.me", "telegra.ph", "medium.com",
    "substack.com", "aistudio.google.com", "mimo.xiaomi.com",
    "qwenintelligence.com", "console.tencentcloud.com", "rg.ru",
    "interspeech2026.org", "the-decoder.ru", "x.com", "twitter.com",
    "linkedin.com", "wikipedia.org", "sberbank.ru", "cbr.ru",
}

SHORTENERS = {"t.co", "clck.ru", "bit.ly", "tinyurl.com", "is.gd", "cutt.ly",
              "short.link", "vk.cc", "u.to", "ow.ly", "goo.gl", "red.ht"}

# Сигналы, которые сами по себе доказывают рекламу. Сокращатель — нет:
# он лишь повод копнуть глубже, поэтому веса не даёт.
STRONG_FLAGS = ("ad_domain", "deep_ad_domain", "private_invite", "utm:",
                "ref_param", "ad_path", "ad_title", "deep_flagged",
                "shortener_target", "track_param:source")

AD_QUERY_KEYS = {"ref", "referrer", "affiliate", "aff", "partner", "promo",
                 "ad_id", "click_id", "source", "campaign", "from"}
AD_UTM_VALUES = {"telegram", "tg", "tik", "t.me", "telemag", "telemetr"}
AD_PATH_RE = re.compile(r"/(?:welcome|register|signup|sign-up|auth|join|"
                        r"start|intro|free|bonus|promo|discount)/", re.I)
PRIVATE_INVITE_RE = re.compile(r"t\.me/\+|t\.me/joinchat/|telegram\.me/joinchat/|"
                               r"(?:^|\s)\+\w{12,}", re.I)


# ── ссылки ────────────────────────────────────────────────────────────────
# Регулярка ищет и в обычном тексте, и внутри href="…"
URL_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"'\)\]\},]+"
    r"|(?<![\w@.])(?:t\.me|telegram\.me|youtu\.be)/[^\s<>\"'\)\]\},]+"
)
# Голые домены в тексте («router.bynara.id»). Список TLD намеренно узкий,
# иначе под регулярку попадут имена файлов и версии вида 2.0.app.
BARE_TLD = ("ai", "top", "site", "dev", "shop", "store", "online", "club",
            "pro", "xyz", "io", "co", "tech", "app", "ru", "com", "net", "org")
BARE_DOMAIN_RE = re.compile(
    r"(?i)(?<![\w.@/:-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:" + "|".join(BARE_TLD) + r")(?![\w-])"
    r"(?:/[^\s<>\"'\)\]\},]*)?")
ANCHOR_SPAN_RE = re.compile(r"""<a\s[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""",
                            re.I | re.S)
ANCHOR_HREF_RE = re.compile(r"""<a\s[^>]*href=["']([^"']+)["']""", re.I)
TAG_RE = re.compile(r"<[^>]+>")



def strip_tags(text):
    t = re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", text or "", flags=re.S)
    t = re.sub(r"</?blockquote[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<a\s+[^>]*>(.*?)</a>", r"\1", t, flags=re.I | re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return t


def _norm_domain(host):
    host = (host or "").lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_links(text):
    """Ссылки поста: домен, текст якоря, флаг «внутри CTA-строки».

    Источники ссылок: значения href в <a>, явные http(s)://, t.me/… и голые
    домены в тексте. href разбираем отдельно — там значение однозначно,
    в отличие от текста, где «2.0.app» не домен.
    """
    raw = text or ""
    spans = []
    for m in ANCHOR_SPAN_RE.finditer(raw):
        spans.append((m.start(), m.end(), m.group(1),
                      TAG_RE.sub("", m.group(2)).strip()))

    lines, pos = [], 0
    for ln in raw.split("\n"):
        lines.append((pos, pos + len(ln), ln))
        pos += len(ln) + 1

    found, seen = [], set()

    def _add(url, anchor, in_anchor, ctx):
        url = (url or "").strip().rstrip(".,;:!?\"')")
        if not url or url.startswith(("#", "mailto:", "tel:", "data:")):
            return
        full = url if url.lower().startswith(("http://", "https://")) else "https://" + url
        try:
            host = urlparse(full).netloc
        except Exception:
            return
        bare = _norm_domain(host)
        if not bare or "." not in bare:
            return
        key = bare + ("|" + (anchor or ""))
        if key in seen:
            return
        seen.add(key)
        ctx_plain = TAG_RE.sub("", ctx or "")
        found.append({
            "url": url, "domain": bare, "anchor": anchor,
            "in_anchor": in_anchor,
            "in_cta": bool(CTA_LINE_RE.search(ctx_plain)),
        })

    # 1) href — самый надёжный признак ссылки
    for m in ANCHOR_HREF_RE.finditer(raw):
        ctx = ""
        for s, e, ln in lines:
            if s <= m.start() <= e:
                ctx = ln
                break
        anchor, in_anchor = "", False
        for s, e, au, al in spans:
            if s <= m.start() < e:
                anchor, in_anchor = al, True
                break
        _add(m.group(1), anchor, in_anchor, ctx)

    # 2) явные ссылки в тексте
    for rx in (URL_RE, BARE_DOMAIN_RE):
        for m in rx.finditer(raw):
            ctx = ""
            for s, e, ln in lines:
                if s <= m.start() <= e:
                    ctx = ln
                    break
            anchor, in_anchor = "", False
            for s, e, au, al in spans:
                if s <= m.start() < e:
                    anchor, in_anchor = al, True
                    break
            _add(m.group(0), anchor, in_anchor, ctx)

    return found


def load_cache():
    try:
        with open(CACHE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_list(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list) and data:
            return [str(x).strip().lower() for x in data if str(x).strip()]
    except Exception:
        pass
    return list(default)


AD_DOMAINS = _load_list(DOMAINS_FILE, DEFAULT_AD_DOMAINS)


def classify_link(link, cache=None):
    """Вердикт по одной ссылке: (is_ad, [флаги])."""
    url, bare = link["url"], link["domain"]
    cache = cache if cache is not None else load_cache()
    flags = []

    if PRIVATE_INVITE_RE.search(url):
        return True, ["private_invite"]

    deep = cache.get(bare) or cache.get(url)
    if isinstance(deep, dict):
        final = (deep.get("final_domain") or "").lower()
        if final and final != bare:
            for ad in AD_DOMAINS:
                if final == ad or final.endswith("." + ad):
                    flags.append("deep_ad_domain:%s" % final)
        if deep.get("ad_title"):
            flags.append("ad_title")
        if deep.get("is_shortener_target") and bare in SHORTENERS:
            flags.append("shortener_target")
    elif isinstance(deep, str) and deep in ("ad", "blocked"):
        flags.append("deep_flagged")

    for ad in AD_DOMAINS:
        if bare == ad or bare.endswith("." + ad):
            flags.append("ad_domain")

    try:
        q = parse_qs(urlparse(url if url.startswith("http") else "https://" + url).query)
    except Exception:
        q = {}
    for k, vals in q.items():
        lk = k.lower()
        low = " ".join(vals).lower()
        if lk.startswith("utm") and low in AD_UTM_VALUES:
            flags.append("utm:%s" % lk)
        elif lk in ("ref", "referrer", "affiliate", "aff", "partner",
                    "promo", "ad_id", "click_id"):
            flags.append("ref_param:%s" % lk)
        elif lk == "source" and low in AD_UTM_VALUES:
            flags.append("track_param:%s" % lk)

    path = urlparse(url if url.startswith("http") else "https://" + url).path
    if AD_PATH_RE.search(path):
        flags.append("ad_path")

    if bare in SHORTENERS:
        flags.append("shortener")

    if bare in TRUSTED_DOMAINS or any(bare.endswith("." + t) for t in TRUSTED_DOMAINS):
        return False, []
    if link.get("in_cta") and flags:
        flags.append("in_cta")
    return bool(flags), flags


# ── фразы: базу берём из adfilter, она проверена на реальных постах ──────
try:
    from adfilter import (PROMO_RE as _BASE_PROMO, CTA_LINE_RE as _BASE_CTA,
                          CHANNEL_FOOTER_RE as _BASE_FOOTER,
                          REACTION_RE as _BASE_REACTION, SEP_RE as _BASE_SEP)
    _BASE_PROMO_SRC = _BASE_PROMO.pattern
    _BASE_CTA_SRC = _BASE_CTA.pattern
    _BASE_FOOTER_SRC = _BASE_FOOTER.pattern
    _BASE_REACTION_SRC = _BASE_REACTION.pattern
    _BASE_SEP_SRC = _BASE_SEP.pattern
except Exception:
    _BASE_PROMO_SRC = _BASE_CTA_SRC = _BASE_FOOTER_SRC = ""
    _BASE_REACTION_SRC = _BASE_SEP_SRC = r"^[=\-—_·•\.]{3,}$"

# Чего не хватало в базе
EXTRA_PHRASE = (
    r"(?:\bдля\s+реклам\w*"
    r"|\bреклам(?:а|у|е)\s+у\s+нас\b"
    r"|\bмы\s+тут\b"
    r"|\bмы\s+здесь\b"
    r"|\bзаходите?\s+к\s+нам\b"
    r"|\bидите\s+к\s+нам\b"
    r"|\bпо\s+поводу\s+реклам\w*"
    r"|\bоплат\w*\s+картами\s+рф\b"
    r"|\bдоступ\w*\s+из\s+россии\b"
    r"|\bбез\s+впн\b"
    r"|\bработает\s+без\s+впн\b"
    r"|\bпишите?\s+в\s+(?:лс|личку|личные\s+сообщения|dm|директ)\b"
    r"|\bв\s+1\s*клик\b"
    r"|\bодним\s+кликом\b"
    r"|\bпромокод\w*"
    r"|\bскидк\w*\s*\d*\s*%"
    r"|\bподписк\w*\s+на\s+канал\b"
    r"|\bпо\s+подписк\w*"
    r"|\bза\s+подписк\w*"
    r"|\bуспейте?\s+(?:купить|подписаться|попробовать)\b"
    r"|\bперв\w*\s+дн\w*\s+бесплатн\w*"
    r"|\bпробн\w*\s+период\b"
    r"|\bбесплатн\w*\s+(?:кредит|токен|генерац)\w*"
    r"|\bбонус\w*\s+(?:кредит|токен|генерац)\w*"
    r"|\bи\s+web[\s\W]{0,20}ресурс\w*"
    r"|\bweb[\s\W]{0,20}ресурс\w*"
    r"|\bбель[её]\s+для\s+промпта\b"
    r"|\bв\s+комментариях\b"
    r"|\bпротестировать\b"
    r"|\bполучите?\s+доступ\b"
    r")"
)

# Второй слой: формулировки, которые не ловились на реальных постах.
EXTRA_PHRASE_2 = (
    r"|\bне\s+нужен\s+(?:vpn|впн)\b"
    r"|\bбез\s+нужды\s+в\s+vpn\b"
    r"|\bоплат\w*\s+(?:российскими\s+)?картами\b"
    r"|\bдоступ\w*\s+к\s+всем\s+модел\w*"
    r"|\bв\s+нашем\s+магазин\w*"
    r"|\bна\s+нашем\s+сайте\b"
    r"|\bв\s+нашем\s+боте\b"
    r"|\bскачать\s+гайд\b"
    r"|\bскачайте\s+гайд\b"
    r"|\bна\s+сайте\s+из\b"
    r"|\bжми\s+сюда\b"
    r"|\bнажми\s+сюда\b"
    r"|\bтыкни\s+сюда\b"
    r"|\bсюда\s+⬅\s*⬅"
    r"|\bхаляв\w*\s+не\s+назвал\w*"
    r"|\bхаляв\w*\s+до\s+конца\b"
    r"|\bподборк\w*\s+в\s+подборк\w*"
    r"|\bсвежие\s+подборк\w*\s+в\s+подборк\w*"
    r"|\bбесплатн\w*\s+доступ\w*\s+к\s+всем\b"
    r"|\bу\s+нас\s+открыт\w*\b"
    r"|\bс\s+чего\s+начать\b"
    r"|\bподарок\s+для\s+старта\b"
    r"|\bв\s+1\s+клик\b"
    r"|\bодним\s+кликом\b"
    r"|\bвсе\s+бесплатно\b"

    # self-promo: свои наборы/курсы/боты (ловится без LLM)
    r"|\bAI\s*BOX\b"
    r"|\bAI[-\s]набор\w*"
    r"|\bсвой\s+(?:набор|курс|гайд|бот|сервис|шрифт)\b"
    r"|\bсобрал[аи]?\s+вс[её]\s+в\s+одном\s+месте\b"
    r"|\bсоздан\s+именно\s+под\s+задачи\b"
    r"|\bкакой\s+нужен\s+именно\s+вам\b"
    r"|\bу\s+меня\s+(?:теперь\s+)?\d*\s*(?:AI\s*)?набор\w*"
    r"|\bпод\s+задачи\s+бухгалтер\w*"
    r"|\bэтот\s+набор\b"
    r"|\bдля\s+тех,\s+кто\s+не\s+знает\b"
)
EXTRA_PHRASE += EXTRA_PHRASE_2

# индонезийский/английский мусор в русскоязычных каналах-агрегаторах
EXTRA_PHRASE += (
    r"|\bdiskon\s*\d*\s*%"
    r"|\bpromo\s+pricing\b"
    r"|\bpromotional\s+pricing\b"
    r"|\bharga\s+promo\b"
    r"|\bdapatkan\b"
    r"|\bgratis\b"
    r"|\bsiap\b"
    r"|\bper\s+1\s+million\s+tokens\b"
    r"|\bper\s+million\s+tokens\b"
    r"|\bnow\s+available\s+on\b"
    r"|\bexplore\s+them\s+at\b"
    r"|\btry\s+(?:it\s+)?for\s+free\b"
    r"|\bfree\s+trial\b"
    r"|\blimited\s+time\b"
    r"|\bact\s+now\b"
    r"|\bget\s+started\b"
    r"|\bsign\s+up\b"
    r"|\bstart\s+your\b"
    r"|\buse\s+code\b"
    r"|\bdiscount\b"
    r"|\bin\s+stock\b"
)

PHRASE_RE = re.compile("(?is)(?:%s)|(?:%s)" % (_BASE_PROMO_SRC, EXTRA_PHRASE))
CTA_LINE_RE = re.compile("(?im)(?:%s)|(?:%s)" % (_BASE_CTA_SRC, (
    r"(?:^|[\W_])(?:заходите|зайди|заходим|заходи|загляните|загляни)\b"
    r"|(?:^|[\W_])(?:переходите|перейди|переходим)\b"
    r"|(?:^|[\W_])идите\s+к\s+нам\b"
    r"|.*\bмы\s+тут\b"
    r"|.*\bмы\s+здесь\b"
    r"|.*\bу\s+нас\s+вы\s+можете\b"
    r"|.*\bподпишитесь\b"
    r"|.*\bдля\s+рекламы\b"
    r"|.*\bжмите\b"
    r"|.*\bнажмите\b"
    r"|.*\bтыкните\b"
    r"|.*\bкликни\b"
    r"|.*\bскачайте\b"
    r"|.*\bпротестировать\b"
    r"|.*\bполучите\s+доступ\b"
)))
FOOTER_RE = re.compile("(?im)(?:%s)" % _BASE_FOOTER_SRC)
REACTION_RE = re.compile("(?im)(?:%s)" % _BASE_REACTION_SRC)
SEP_RE = re.compile("(?im)(?:%s)" % _BASE_SEP_SRC)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _plain_sentences(text):
    out = []
    for ln in strip_tags(text).split("\n"):
        for s in SENT_SPLIT_RE.split(ln):
            s = s.strip()
            if s:
                out.append(s)
    return out


def _is_strong(flags):
    return any(c.startswith(STRONG_FLAGS) for c in flags)


def analyze(text, cache=None, deep=False, llm=None):
    """Полный разбор поста: ссылки, фразы, вес, вердикт.

    deep=True — реально обходим каждую ссылку (редиректы, title, тело) и
    добавляем сигналы посадочной страницы. Медленно, поэтому кладётся
    в кэш и гоняется только для постов с признаками рекламы.
    llm=callable — второе мнение поста (обычно GLM), см. llm_verdict().
    """
    cache = cache if cache is not None else load_cache()
    plain = strip_tags(text or "")
    partial = False

    links = extract_links(text or "")

    # ── проход 1: только статика, без сети ───────────────────────────────
    link_flags = []
    for ln in links:
        is_ad, flags = classify_link(ln, cache)
        link_flags.append({"url": ln["url"], "domain": ln["domain"],
                           "anchor": ln.get("anchor", ""),
                           "in_cta": ln.get("in_cta", False),
                           "is_ad": is_ad, "strong": _is_strong(flags),
                           "final_domain": None, "final_url": None,
                           "link_score": 0, "flags": flags})

    phrase_flags = []
    for s in _plain_sentences(plain):
        m = PHRASE_RE.search(s)
        if m:
            phrase_flags.append({"phrase": (m.group(0) or "").strip()[:60],
                                 "sentence": s[:140]})

    def _score(flags_list, phs):
        sc = 0
        for f in flags_list:
            if not f["strong"]:
                continue
            sc += 45
            sc += 15 * sum(1 for c in f["flags"] if c.startswith(STRONG_FLAGS))
        sc += 15 * len(phs)
        if any(f["in_cta"] and f["strong"] for f in flags_list):
            sc += 25
        return sc

    # ── проход 2: в сеть идём только если пост уже подозрителен ──────────
    # Иначе 30 чистых новостей тоже дёргали бы сеть и съедали цикл бота.
    static_score = _score(link_flags, phrase_flags)
    deep_links = bool(links) and (static_score > 0 or not phrase_flags is None)
    if deep and links and static_score >= 20:
        for f in link_flags:
            if f["strong"] or f["is_ad"]:
                continue
            if _expired():
                partial = True
                break
            ln = {"url": f["url"]}
            deep_info = _deep_link(ln, cache)
            if deep_info:
                f["flags"] = f["flags"] + deep_info["flags"]
                f["strong"] = _is_strong(f["flags"])
                f["is_ad"] = f["is_ad"] or bool(deep_info["strong"])
                f["final_url"] = deep_info["final_url"]
                f["final_domain"] = deep_info["final_domain"]
                f["link_score"] = deep_info["score"]
    elif deep and links:
        # не подозрение — сеть не трогаем
        deep_links = False

    score = _score(link_flags, phrase_flags)

    # сигналы посадочной подмешиваем только когда реально сходили
    if deep_links:
        for f in link_flags:
            if f.get("link_score", 0) >= LINK_SCORE_MIN:
                score += min(60, f["link_score"] // 2)

    rep = {"links": link_flags, "phrases": phrase_flags, "score": score,
           "verdict": "drop" if score >= DROP_SCORE else "clean",
           "strong": bool(any(f["strong"] for f in link_flags) or phrase_flags),
           "ad_links": sorted({f["url"] for f in link_flags if f["strong"]})}
    if score >= DROP_SCORE:
        rep["verdict"] = "drop"

    # ── этап 4: второе мнение ────────────────────────────────────────────
    # LLM зовём только когда пост уже выглядит подозрительно, и результат
    # кэшируем по хэшу текста: цикл бота крутится каждые 2 секунды.
    if llm is not None and rep["verdict"] != "drop" and rep["score"] > 0:
        if _expired():
            partial = True
        else:
            v = llm_verdict(plain, rep)
            if v == "drop":
                rep["score"] = max(rep["score"], DROP_SCORE)
                rep["verdict"] = "drop"
                rep["strong"] = True
                rep["llm"] = "drop"
            elif v == "trim":
                # реклама есть, но содержание реальное — чистим, не выкидываем
                rep["llm"] = "trim"
    rep["partial"] = partial
    return rep


def _deep_link(ln, cache):
    """Реально проверяем, куда ведёт ссылка. Только если не поймали раньше."""
    try:
        import linkcheck
    except Exception:
        return None
    try:
        r = linkcheck.check(ln["url"], cache)
    except Exception:
        return None
    if r.get("error") in ("no_domain", "bad_url", "bad_scheme",
                          "bad_port", "ssrf_blocked"):
        return None
    flags = list(r.get("flags") or [])
    strong = bool(r.get("score", 0) >= LINK_SCORE_MIN)
    if not flags and not strong:
        return None
    return {"flags": ["deep:" + c for c in flags], "strong": strong,
            "score": r.get("score", 0),
            "final_url": r.get("final_url"), "final_domain": r.get("domain")}


def llm_verdict(plain, rep):
    """Второе мнение GLM по подозрительному посту.

    Три ответа, а не два — это принципиально:
      drop — пост целиком реклама, показывать нечего;
      trim — реклама вперемешку с полезным содержимым, почистить;
      ok   — обычный пост.

    Двухзначный «AD/OK» путал новости о сервисах с рекламой и просил
    выкидывать посты, где рекламный хедер, а под ним полезный промпт.

    None — мост не ответил; тогда молча пропускаем, чтобы отсутствие
    GLM не роняло отправку.
    """
    import hashlib
    import os
    import json
    import urllib.request

    # Автомат: если апстрим режет IP (503 waf_block), не долбим минуты подряд.
    st = _glm_state()
    if st["down_until"] > time.time():
        return None

    key_id = hashlib.sha1(plain.strip().encode("utf-8")).hexdigest()[:16]
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "ad_llm_cache.json")
    try:
        with open(cache_path, encoding="utf-8") as f:
            llm_cache = json.load(f)
        if not isinstance(llm_cache, dict):
            llm_cache = {}
    except Exception:
        llm_cache = {}
    if key_id in llm_cache:
        return llm_cache[key_id] or None

    base = os.environ.get("GLM_BASE", "http://127.0.0.1:3001")
    key = os.environ.get("GLM_API_KEY", "glm-local")
    if not key:
        return None

    body = plain[:2500]
    prompt = (
        "Текст поста из Telegram-канала про нейросети. Ответь ОДНИМ словом.\n\n"
        "DROP — пост целиком рекламный: продаёт сервис/подписку/курс/доступ, "
        "просит подписаться или перейти, и полезного содержания в нём нет.\n"
        "TRIM — в посте есть рекламная часть, но рядом есть настоящее "
        "содержание (новость, инструкция, шаблон промпта, описание сервиса). "
        "Рекламу надо вырезать, остальное оставить.\n"
        "OK — обычный пост, рекламы нет.\n\n"
        "ВАЖНО: новость О СЕРВИСЕ — это не реклама. Если пост рассказывает, "
        "что вышел новый сервис или что он делает, даже если в конце ведёт "
        "на его сайт, это TRIM, а не DROP. То же самое с рекламной шапкой "
        "в начале поста, если дальше идёт полезное содержимое.\n\n"
        "ПОСТ:\n" + body
    )
    data = {
        "model": os.environ.get("GLM_MODEL", "glm-5.3"),
        "messages": [{"role": "user", "content": prompt}],
        # bridge по умолчанию отдаёт SSE-стрим; нам нужен обычный JSON
        "stream": False,
        # GLM сначала пишет reasoning_content, поэтому ответ не в 8 токенов
        "max_tokens": 600, "temperature": 0,
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=GLM_TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
        if not raw.lstrip().startswith("{"):
            return None
        out = json.loads(raw)
        txt = (out["choices"][0]["message"].get("content") or "").strip().upper()
    except Exception as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        _glm_failed(body or type(exc).__name__)
        return None

    _glm_ok()

    if txt.startswith("DROP"):
        verdict_out = "drop"
    elif txt.startswith("TRIM"):
        verdict_out = "trim"
    elif txt.startswith("OK"):
        verdict_out = "ok"
    else:
        verdict_out = None

    try:
        llm_cache[key_id] = verdict_out
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(llm_cache, f)
        os.replace(tmp, cache_path)
    except Exception:
        pass
    return verdict_out



def _sent_is_ad(sentence, ad_hosts):
    if CTA_LINE_RE.match(sentence) or PHRASE_RE.search(sentence):
        return True
    low = sentence.lower()
    return any(h and h in low for h in ad_hosts)


def clean(text, report=None):
    """Вырезает рекламу, оставляя содержание. Возвращает (текст, отчёт)."""
    rep = report if report is not None else analyze(text)
    ad_hosts = {d["domain"] for d in rep["links"] if d["is_ad"]}
    plain = strip_tags(text or "")

    kept, removed = [], 0
    for ln in plain.split("\n"):
        raw = ln.strip()
        if not raw:
            kept.append("")
            continue
        if SEP_RE.match(raw) or FOOTER_RE.match(raw) or REACTION_RE.match(raw):
            removed += 1
            continue
        if CTA_LINE_RE.match(raw):
            removed += 1
            continue
        low = raw.lower()
        if any(h in low for h in ad_hosts):
            removed += 1
            continue
        if PHRASE_RE.search(raw):
            good = [p.strip() for p in SENT_SPLIT_RE.split(raw)
                    if p.strip() and not _sent_is_ad(p, ad_hosts)]
            if not good:
                removed += 1
                continue
            kept.append(" ".join(good))
            continue
        kept.append(raw)

    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    out = re.sub(r"[ \t]{2,}", " ", out)
    sents = _plain_sentences(out)
    rep["removed_lines"] = removed
    rep["removed_phrases"] = len(rep["phrases"])
    rep["removed_links"] = len(rep["ad_links"])
    rep["kept_chars"] = len(out)
    rep["kept_sents"] = len(sents)
    # выкидываем только если реклама доказана и после чистки ничего не осталось
    rep["ad_only"] = bool(rep.get("strong") and
                          (len(out) < MIN_CHARS or len(sents) < MIN_SENTS))
    if rep["ad_only"]:
        rep["verdict"] = "drop"
    rep["touched"] = bool(removed or rep["ad_links"])
    return out, rep


def gate(post, cache=None, deep=False, llm=None):
    """Гейт поста из очереди → (вердикт, отчёт, чистый_текст).

    'drop' — не показывать человеку; 'trim' — показать с очисткой;
    'keep' — показать как есть.

    deep=True дёргает сеть только для тех постов, у которых уже есть
    хоть один признак рекламы: чистые новости по сети не ходят.
    llm=True подключает GLM как второе мнение.
    """
    raw = post.get("text") or ""
    rep = analyze(raw, cache, deep=deep, llm=llm)
    if rep["verdict"] == "drop":
        return "drop", rep, ""
    cleaned, rep = clean(raw, rep)
    if rep["verdict"] == "drop":
        return "drop", rep, cleaned
    if rep["touched"]:
        return "trim", rep, cleaned
    return "keep", rep, raw
