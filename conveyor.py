#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""conveyor.py — конвейер контента: источники → адаптация → очередь.

Схема:
  1. --ingest   : тянет посты из t.me/s/<канал> (через прокси), сразу скачивает
                  картинки постов в media/, пишет всё в content.db (дедуп: md5 + SimHash)
  2. --adapt N  : берёт свежие неиспользованные посты (без новостей), переписывает
                  через GLM-бридж в наш evergreen-формат и кладёт черновики в queue.json
  3. --stats    : что накопилось в базе

Примеры:
    python3 conveyor.py --ingest
    python3 conveyor.py --adapt 3 --channel neuro_secrets
    python3 conveyor.py --stats
"""

import argparse
import hashlib
import html as html_mod
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "content.db")
CONFIG = os.path.join(HERE, "sources.json")
QUEUE = os.path.join(HERE, "queue.json")
MEDIA = os.path.join(HERE, "media")
MSK = ZoneInfo("Europe/Moscow")
CHANNELS_CONFIG = os.path.join(HERE, "config", "CHANNELS.json")
CHANNEL_MODULES = os.path.join(HERE, "prompts", "CHANNEL_MODULES.md")
CHANNEL_TITLES = {"neuro_secrets": "Нейро-секреты", "wizard_prompts": "Волшебные промпты",
                  "ai_news": "Нейро-Пульс", "neuro_image": "Нейро-образ",
                  "neuro_work": "Нейро-работа", "neuro_fun": "Нейро-приколы"}
GLOBAL_RULES_FILE = os.path.join(HERE, "prompts", "00_GLOBAL_RULES.md")

QC_SYSTEM = """Ты — финальный контролёр качества контента. Оцени материал, не переписывая его.
Верни строго JSON:
{"value": 0-10, "originality": 0-10, "clarity": 0-10, "actionability": 0-10, "hook": 0-10,
 "saveability": 0-10, "shareability": 0-10, "naturalness": 0-10, "repetition": 0-10, "risk": 0-10,
 "status": "PASS|REWORK|REJECT", "reason": "коротко",
 "content_dna": {"problem": "...", "mechanic": "prompt|before_after|checklist|story|tools",
                 "hook_type": "боль|любопытство|результат|ошибка|наблюдение"}}
Правила: PASS если value>=7, clarity>=7, actionability>=7, repetition<=3, risk<=3.
REWORK если 5–6 по ключевым. REJECT если value<5 или risk>=7 или repetition>=7."""

ADAPTER_SYSTEM = """Ты — редактор Telegram-каналов «Нейро-секреты» и «Волшебные промпты».
Аудитория: {audience}. Тон: {tone}. Аудитория — обычные женщины, НЕ разработчики.

Разделение каналов (важно!):
- neuro_secrets «Нейро-секреты»: жизненные и рабочие задачи — резюме, письма, учёба,
  документы, быт, контент, связки инструментов + алгоритмы, подборки инструментов и разборы новинок.
- wizard_prompts «Волшебные промпты»: библиотека промптов — картинки, фото, дизайн, обложки, тексты.
Если материал не подходит ни одному каналу — верни {{"skip": true}}.

ФОРМАТ поста: hook (заход) → суть → польза → действие (промпт/шаг/ссылка).
ДЛИНА по рубрике: секрет 300–600, промпт 300–800, до/после 400–900, интерактив 150–400,
лайфхак 300–700, подборка/разбор 500–1000 знаков.
ЭМОДЗИ 0–4 — только по структуре, не механически. ХЭШТЕГИ 0–3 (иногда можно без них).

ЗАПРЕЩЕНО:
- даты и относительные даты: «сегодня», «завтра», «вчера», «на этой неделе», «в этом месяце»,
  «сейчас», «недавно», «только что», «скоро» → заменяй на «когда понадобится», «в любой момент»;
- новости, «вышла/обновили/только что», версии, свежие релизы, цены, тарифы, статистика;
- обещания: «гарантированно», «всегда», «никогда», «100%», «идеально» как обещание результата;
- медицинские, финансовые, юридические рекомендации;
- агенты, API, код, системные промпты — не наша аудитория.

МОЖНО: названия инструментов и официальные ссылки (сайт/GitHub), без партнёрских и тарифов.

Ответ строго JSON без пояснений:
{{"channel": "neuro_secrets или wizard_prompts", "rubric": "Секрет дня", "text": "готовый пост",
 "hashtags": ["#..."], "visual_required": true, "visual_concept": "что на картинке",
 "image_prompt": "описание картинки по-русски", "image_prompt_en": "same in english",
 "content_dna": {{"problem": "какую задачу решает", "mechanic": "prompt|before_after|checklist|story|tools",
 "hook_type": "боль|любопытство|результат|ошибка|наблюдение"}}}}"""

NEWS_MARKERS = ("выпустил", "представил", "анонсировал", "релиз", "обновил", "версия",
                "раунд", "инвестиц", "стоимость акций", "подорожал", "подешевел", "запустила новую")
PRACTICAL_MARKERS = ("промпт", "запрос", "попробуй", "совет", "лайфхак", "как ", "способ",
                     "секрет", "используй", "пример", "шаг", "сделай")


def load_config():
    return json.load(open(CONFIG, encoding="utf-8"))


def load_channel_configs():
    data = load_json(CHANNELS_CONFIG, {})
    if "channels" in data:
        return {ch["id"]: ch for ch in data["channels"]}
    return data


MODULE_FILES = {"neuro_secrets": "CHANNEL_NEURO_SECRET.md", "wizard_prompts": "CHANNEL_MAGIC_PROMPTS.md",
                "neuro_image": "CHANNEL_NEURO_IMAGE.md", "neuro_work": "CHANNEL_NEURO_WORK.md",
                "neuro_fun": "CHANNEL_NEURO_FUN.md"}


def channel_module(channel):
    """Level-2: модуль конкретного канала (отдельный файл)."""
    fname = MODULE_FILES.get(channel)
    if not fname:
        return ""
    try:
        return open(os.path.join(HERE, "prompts", fname), encoding="utf-8").read()
    except FileNotFoundError:
        return ""


def load_global_rules():
    try:
        return open(GLOBAL_RULES_FILE, encoding="utf-8").read()
    except FileNotFoundError:
        return ""


def text_tokens(text):
    return set(re.findall(r"[а-яёa-z0-9]{4,}", (text or "").lower()))


def similarity(a, b):
    ta, tb = text_tokens(a), text_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def load_history():
    """История: очередь + последние архивы (для анти-повтора)."""
    history = []
    files = ["queue.json"] + sorted(f for f in os.listdir(HERE) if f.startswith("queue_archive_"))[-2:]
    for fname in files:
        for post in load_json(os.path.join(HERE, fname), []):
            if post.get("text"):
                history.append(post)
    return history


def top_similar(text, history, n=8):
    scored = [(similarity(text, p.get("text", "")), p) for p in history]
    scored = [row for row in scored if row[0] > 0.10]
    scored.sort(key=lambda row: -row[0])
    return scored[:n]


def channel_publishes(channel):
    """Публикуется ли канал сейчас (иначе посты копятся как library)."""
    ch = load_channel_configs().get(channel) or {}
    return bool(ch.get("publish", True))


def post_status(channel):
    return "pending" if channel_publishes(channel) else "library"


def learning_rules():
    """Правила от Learning Loop (content_memory.json)."""
    mem = load_json(os.path.join(HERE, "content_memory.json"), {})
    rules = mem.get("rules") or []
    return "\n".join("- %s" % r for r in rules[:15])


def build_system_prompt(channel=None):
    """Level-1 (правила) + Level-2 (канал) + схема ответа."""
    rules = load_global_rules()
    chans = load_channel_configs()
    lines = []
    for key, ch in chans.items():
        c = ch.get("channel") or ch
        lines.append("- %s «%s» (%s): %s | рубрики: %s | тон: %s" % (
            key, c.get("name"), ch.get("role") or ch.get("content", {}).get("mode"),
            ch.get("positioning") or c.get("description"),
            ", ".join(ch.get("rubrics") or ch.get("content", {}).get("rubrics") or []),
            c.get("tone")))
    module = channel_module(channel) if channel else ""
    schema = ('Ответ строго JSON: {"channel": "neuro_secrets или wizard_prompts", "rubric": "...", '
              '"text": "...", "hashtags": ["#..."], "visual_required": true, "visual_concept": "...", '
              '"image_prompt": "...", "image_prompt_en": "..."}')
    rules_mem = learning_rules()
    return "%s\n\n## ПРАВИЛА НЕДЕЛИ (от аналитики)\n%s\n\n## КАНАЛЫ СЕТИ\n%s\n\n## МОДУЛЬ КАНАЛА\n%s\n\n## ВЫХОД\n%s" % (
        rules, rules_mem or "(нет)", "\n".join(lines), module or "(общий)", schema)


def quality_check(cfg, text, history):
    """QC: скоринг + решение (PASS/REWORK/REJECT) + DNA."""
    similar = top_similar(text, history, 5)
    context = "\n".join("- %s" % p.get("text", "")[:120].replace("\n", " ") for _, p in similar)
    try:
        reply = bridge_chat(cfg, QC_SYSTEM, "Материал:\n\n%s\n\nПохожие из истории:\n%s" % (text, context or "-"))
    except Exception as exc:
        return {"status": "PASS", "reason": "QC недоступен: %s" % str(exc)[:60]}
    data = parse_json_reply(reply)
    if not data:
        return {"status": "PASS", "reason": "QC не распарсился"}
    return data


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def fetch(url, proxy, timeout=40):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_bytes(url, proxy, timeout=60):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def simhash(text):
    """64-битный SimHash по словам текста."""
    tokens = re.findall(r"[а-яёa-z0-9]{3,}", (text or "").lower())
    if not tokens:
        return 0
    vector = [0] * 64
    for token in set(tokens):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(64):
            vector[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if vector[i] > 0:
            out |= (1 << i)
    return out


def hamming(a, b):
    return bin((a or 0) ^ (b or 0)).count("1")


def to_signed(value):
    value &= 0xFFFFFFFFFFFFFFFF
    return value - (1 << 64) if value >= (1 << 63) else value


def from_signed(value):
    return (value or 0) & 0xFFFFFFFFFFFFFFFF


def is_duplicate(conn, sh, max_dist=10, limit=800):
    rows = conn.execute("SELECT simhash FROM items ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return any(hamming(sh, from_signed(r[0])) <= max_dist for r in rows if r[0])


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u3000-\u303f\uff00-\uffef]")


def looks_garbage(text):
    """Грубая проверка на «поехавший» текст или иероглифы."""
    if not text:
        return True
    if CJK_RE.search(text):
        return True
    if re.search(r"[<>|\\{}]", text):
        return True
    probe = text.replace("_", "")
    if re.search(r"[!-/:-@\[-`{-~]{4,}", probe):
        return True
    if re.search(r"[A-Za-zА-Яа-яЁё]{28,}", text):
        return True
    if text.count("«") != text.count("»"):
        return True
    return False


def db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, post_id TEXT, text TEXT, ts TEXT, views INTEGER,
        image_url TEXT, image_local TEXT DEFAULT '', used INTEGER DEFAULT 0,
        hash TEXT UNIQUE, simhash INTEGER DEFAULT 0, dupe INTEGER DEFAULT 0)""")
    for column in ("simhash INTEGER DEFAULT 0", "dupe INTEGER DEFAULT 0", "image_local TEXT DEFAULT ''"):
        try:
            conn.execute("ALTER TABLE items ADD COLUMN %s" % column)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def parse_views(text):
    text = (text or "").strip().replace("\u00a0", " ")
    m = re.match(r"([\d.,]+)\s*([KkMm]?)", text)
    if not m:
        return 0
    num = float(m.group(1).replace(",", "."))
    mult = {"": 1, "k": 1000, "K": 1000, "m": 1000000, "M": 1000000}[m.group(2)]
    return int(num * mult)


def parse_tme(page, source):
    """Парсит публичную страницу t.me/s/<канал>."""
    items = []
    chunks = re.split(r'<div class="tgme_widget_message[ ")]', page)
    for chunk in chunks[1:]:
        pid = re.search(r'data-post="([^"]+)"', chunk)
        text_m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>\s*(?:<div class="tgme_widget_message_(?:footer|reply)|</div>)', chunk, re.S)
        if not text_m:
            text_m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', chunk, re.S)
        raw = text_m.group(1) if text_m else ""
        raw = re.sub(r"<br\s*/?>", "\n", raw)
        raw = re.sub(r"<[^>]+>", "", raw)
        text = html_mod.unescape(raw).strip()
        if len(text) < 120:
            continue
        dt = re.search(r'<time datetime="([^"]+)"', chunk)
        views = re.search(r'tgme_widget_message_views[^>]*>([^<]+)<', chunk)
        photos = re.findall(r"background-image:url\('([^']+)'\)", chunk)
        photo = next((u for u in photos if "telegram.org/img/emoji" not in u), "")
        items.append({
            "source": source,
            "post_id": pid.group(1) if pid else "",
            "text": text,
            "ts": dt.group(1) if dt else "",
            "views": parse_views(views.group(1)) if views else 0,
            "image_url": photo,
        })
    return items


def parse_github(page):
    """GitHub Trending: репозитории дня."""
    items = []
    for chunk in page.split("Box-row")[1:]:
        m = re.search(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"', chunk)
        if not m:
            continue
        repo = m.group(1)
        if repo.split("/")[0] in ("login", "sponsors", "trending", "features", "topics", "collections", "settings"):
            continue
        desc = re.search(r'<p class="col-9 color-fg-muted my-1 pr-4">\s*(.*?)</p>', chunk, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', chunk)
        stars = re.search(r'([\d,]+)\s*stars today', chunk)
        text = "GitHub: %s — %s%s%s" % (
            repo,
            re.sub(r"\s+", " ", (desc.group(1) if desc else "")).strip()[:300],
            (" (" + lang.group(1).strip() + ")") if lang else "",
            (", +%s звёзд за день" % stars.group(1)) if stars else "")
        items.append({"source": "github_trending", "post_id": repo, "text": text,
                      "ts": datetime.now(MSK).isoformat(), "views": 1000, "image_url": ""})
    return items


def parse_hn(data):
    """Hacker News: главные истории."""
    items = []
    for hit in ((data or {}).get("hits") or []):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        url = hit.get("url") or ("https://news.ycombinator.com/item?id=%s" % hit.get("objectID"))
        text = "Hacker News: %s — %s (очков: %s)" % (title, url, hit.get("points") or 0)
        items.append({"source": "hacker_news", "post_id": str(hit.get("objectID")), "text": text,
                      "ts": datetime.now(MSK).isoformat(), "views": int(hit.get("points") or 0) * 10,
                      "image_url": ""})
    return items


def fetch_source(src, proxy):
    """Возвращает посты источника в зависимости от типа."""
    kind = src.get("kind", "tg")
    name = src["name"]
    if kind == "github":
        url = src.get("url") or "https://github.com/trending?since=daily"
        return parse_github(fetch(url, proxy, timeout=30))
    if kind == "hn":
        data = json.loads(fetch("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20", proxy, timeout=25))
        return parse_hn(data)
    page = fetch("https://t.me/s/%s" % urllib.parse.quote(name), proxy)
    return parse_tme(page, name)


def download_media(conn, source, post_id, image_url, proxy, item_id=None):
    """Скачивает картинку поста в media/ и возвращает относительный путь."""
    if not image_url or not image_url.startswith("http"):
        return ""
    name = re.sub(r"[^\w.-]", "_", "%s-%s" % (source, post_id))[:90] + ".jpg"
    dest = os.path.join(MEDIA, name)
    rel = "media/" + name
    if not os.path.exists(dest):
        try:
            blob = fetch_bytes(image_url, proxy)
            if len(blob) < 3000:
                return ""
            os.makedirs(MEDIA, exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(blob)
        except Exception:
            return ""
    if item_id:
        conn.execute("UPDATE items SET image_local = ?, image_url = ? WHERE id = ?", (rel, image_url, item_id))
    return rel


def cmd_ingest(cfg):
    conn = db()
    stale = conn.execute("SELECT id, text FROM items WHERE simhash = 0").fetchall()
    for item_id, text in stale:
        conn.execute("UPDATE items SET simhash = ? WHERE id = ?", (to_signed(simhash(text)), item_id))
    if stale:
        conn.commit()
        print("дозаполнен simhash у %d старых записей" % len(stale))
    total = new = dupes = media = 0
    for src in cfg["sources"]:
        name = src["name"]
        try:
            items = fetch_source(src, cfg.get("proxy"))
        except Exception as exc:
            print("✗ %-24s %s: %s" % (name, type(exc).__name__, str(exc)[:60]))
            continue
        fresh = 0
        for it in items:
            # 1) этот же пост уже есть? (матч по стабильному post_id) — обновляем фото
            row = conn.execute("SELECT id, image_local FROM items WHERE source = ? AND post_id = ?",
                               (name, it["post_id"])).fetchone()
            if row:
                if not row[1] and it["image_url"]:
                    if download_media(conn, name, it["post_id"], it["image_url"], cfg.get("proxy"), row[0]):
                        media += 1
                continue
            # 2) точный дубль текста из другого канала
            h = hashlib.md5(it["text"][:400].encode("utf-8")).hexdigest()
            if conn.execute("SELECT 1 FROM items WHERE hash = ?", (h,)).fetchone():
                continue
            # 3) похожий текст (SimHash)
            sh = simhash(it["text"])
            if is_duplicate(conn, sh):
                dupes += 1
                continue
            cur = conn.execute(
                "INSERT INTO items (source, post_id, text, ts, views, image_url, hash, simhash, dupe) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (it["source"], it["post_id"], it["text"], it["ts"], it["views"], it["image_url"], h, to_signed(sh)))
            if download_media(conn, name, it["post_id"], it["image_url"], cfg.get("proxy"), cur.lastrowid):
                media += 1
            new += 1
            fresh += 1
        total += len(items)
        conn.commit()
        print("✓ %-24s постов %-4d новых %d" % (name, len(items), fresh))
        time.sleep(1)
    conn.commit()
    print("итого: собрано %d, новых %d, похожих пропущено %d, картинок %d" % (total, new, dupes, media))
    return 0


def cmd_stats(cfg):
    conn = db()
    rows = conn.execute("SELECT source, COUNT(*), SUM(used), SUM(CASE WHEN image_local != '' THEN 1 ELSE 0 END) "
                        "FROM items GROUP BY source ORDER BY COUNT(*) DESC").fetchall()
    print("%-24s %8s %8s %8s" % ("источник", "постов", "использ.", "с фото"))
    for source, cnt, used, photos in rows:
        print("%-24s %8d %8d %8d" % (source, cnt, used or 0, photos or 0))
    total = conn.execute("SELECT COUNT(*), SUM(used), SUM(CASE WHEN image_local != '' THEN 1 ELSE 0 END) FROM items").fetchone()
    print("---")
    print("%-24s %8d %8d %8d" % ("ВСЕГО", total[0] or 0, total[1] or 0, total[2] or 0))
    return 0


def candidate_score(text, views):
    low = (text or "").lower()
    score = views or 0
    if any(m in low for m in PRACTICAL_MARKERS):
        score += 20000
    if any(m in low for m in NEWS_MARKERS):
        score -= 150000
    return score


def bridge_chat(cfg, system, user, timeout=180, retries=2):
    """Запрос к LLM с фолбэком по провайдерам (GLM → DeepSeek → ...)."""
    providers = cfg.get("providers") or [{
        "url": cfg.get("bridge"), "token": cfg.get("bridge_token"), "model": cfg.get("model")}]
    payload_base = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.7,
        "max_tokens": 900,
        "stream": False,
    }
    last_error = None
    for provider in providers:
        payload = dict(payload_base)
        payload["model"] = provider.get("model", "glm-4.7")
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    provider["url"], data=json.dumps(payload).encode("utf-8"),
                    headers={"Authorization": "Bearer " + provider.get("token", ""),
                             "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", "ignore"))
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content
                last_error = RuntimeError("пустой ответ от %s" % provider.get("url"))
            except Exception as exc:
                last_error = exc
                time.sleep(3)
    raise last_error


def parse_json_reply(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None



def add_to_queue(new_posts):
    """Добавляет посты в очередь, не трогая существующие (защита от гонки)."""
    if not new_posts:
        return
    current = load_json(QUEUE, [])
    by_id = {p.get("id"): p for p in current}
    for post in new_posts:
        if post.get("id") not in by_id:
            current.append(post)
    save_json(QUEUE, current)

def next_slots(queue, channel, count):
    """Возвращает следующие слоты публикации (09/14/19 или 10/15/20 МСК)."""
    hours = (9, 14, 19) if channel == "neuro_secrets" else (10, 15, 20)
    now = datetime.now(MSK)
    last = now
    for post in queue:
        if post.get("channel") != channel:
            continue
        try:
            when = datetime.fromisoformat(post["scheduled_at"])
        except Exception:
            continue
        if when > last:
            last = when
    slots = []
    cursor = last
    while len(slots) < count:
        cursor = (cursor + timedelta(minutes=1)).replace(second=0, microsecond=0)
        if cursor.hour in hours and cursor.minute == 0:
            slots.append(cursor)
        elif cursor.hour > max(hours):
            cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0)
    return slots


def cmd_adapt(cfg, count, channel, auto=False):
    conn = db()
    queue = load_json(QUEUE, [])
    prof = cfg["channels"][channel]
    system = build_system_prompt()
    history = load_history()
    rows = conn.execute(
        "SELECT id, source, text, views, image_url, image_local FROM items "
        "WHERE used = 0 AND dupe = 0 AND LENGTH(text) > 200 ORDER BY views DESC, id DESC LIMIT 250").fetchall()
    scored = [(candidate_score(text, views), item_id, source, text, views, image_url, image_local)
              for item_id, source, text, views, image_url, image_local in rows]
    scored = [row for row in scored if row[0] > 0]
    scored.sort(reverse=True)
    # микс: чередуем источники
    by_source = {}
    for row in scored:
        by_source.setdefault(row[2], []).append(row)
    ordered = []
    while len(ordered) < count * 3 and any(by_source.values()):
        for source in list(by_source.keys()):
            bucket = by_source[source]
            if bucket:
                ordered.append(bucket.pop(0))
            if len(ordered) >= count * 3:
                break
    planned = {ch: 0 for ch in cfg["channels"]}
    queue_hashes = {ch: [simhash(p.get("text", "")) for p in queue if p.get("channel") == ch]
                    for ch in cfg["channels"]}
    new_posts = []

    def take_slot(ch):
        planned[ch] += 1
        return next_slots(queue, ch, planned[ch])[-1]

    made = 0
    for score, item_id, source, text, views, image_url, image_local in ordered:
        if made >= count:
            break
        similar = top_similar(text, history, 6)
        avoid = "\n".join("- %s" % p.get("text", "")[:140].replace("\n", " ") for _, p in similar)
        user_msg = "Источник: %s\n\n%s" % (source, text[:2500])
        if avoid:
            user_msg += "\n\nНЕ ПОВТОРЯЙ (уже было в канале):\n%s" % avoid
        try:
            reply = bridge_chat(cfg, system, user_msg)
        except Exception as exc:
            print("✗ адаптация %s: %s: %s" % (source, type(exc).__name__, str(exc)[:80]))
            continue
        draft = parse_json_reply(reply)
        if not draft:
            print("✗ %s: не JSON — оставляю на повтор" % source)
            continue
        if draft.get("skip"):
            conn.execute("UPDATE items SET used = 1 WHERE id = ?", (item_id,))
            conn.commit()
            print("· пропуск (%s): нет вечной сути" % source)
            continue
        if looks_garbage(draft.get("text", "")):
            print("✗ %s: мусор в тексте — оставляю на повтор" % source)
            continue
        conn.execute("UPDATE items SET used = 1 WHERE id = ?", (item_id,))
        conn.commit()
        target = channel
        if auto and draft.get("channel") in cfg["channels"]:
            target = draft["channel"]
        sh = simhash(draft.get("text", ""))
        if any(hamming(sh, qh) <= 10 for qh in queue_hashes[target]):
            print("· повтор (%s): похоже на уже готовый пост — пропуск" % source)
            continue
        queue_hashes[target].append(sh)
        qc = quality_check(cfg, draft.get("text", ""), history)
        qc_status = (qc.get("status") or "PASS").upper()
        if qc_status != "PASS":
            print("· QC %s (%s): %s" % (qc_status, source, (qc.get("reason") or "")[:70]))
            if qc_status == "REJECT":
                conn.execute("UPDATE items SET used = 1 WHERE id = ?", (item_id,))
                conn.commit()
            continue
        slot = take_slot(target)
        base_id = "%s-%s-%s" % ("ns" if target == "neuro_secrets" else "wp",
                                slot.strftime("%Y-%m-%d"), slot.strftime("%H%M"))
        existing_ids = {p.get("id") for p in load_json(QUEUE, [])}
        post_id = base_id
        suffix = 2
        while post_id in existing_ids:
            post_id = "%s-%d" % (base_id, suffix)
            suffix += 1
        image_path = ""
        if draft.get("visual_required", True):
            image_path = image_local or ""
            if not image_path and image_url:
                image_path = download_media(conn, source, "", image_url, cfg.get("proxy"))
        new_posts.append({
            "id": post_id,
            "channel": target,
            "scheduled_at": slot.isoformat(),
            "rubric": draft.get("rubric") or "Промпт дня",
            "text": draft.get("text", "").strip(),
            "hashtags": draft.get("hashtags") or [],
            "image": image_path,
            "source_image": image_url or "",
            "status": post_status(target),
            "check": {"evergreen": True, "editor": "conveyor"},
            "quality": {k: qc.get(k) for k in ("value", "originality", "clarity", "actionability",
                                               "hook", "saveability", "shareability", "naturalness",
                                               "repetition", "risk")},
            "content_dna": qc.get("content_dna") or draft.get("content_dna") or {},
            "visual_concept": draft.get("visual_concept") or "",
            "origin": source,
        })
        made += 1
        print("✓ %s [%s] ← %s | QC value=%s save=%s | фото: %s" % (
            post_id, target, source, qc.get("value"), qc.get("saveability"),
            "да" if image_path else "нет"))
        time.sleep(2)
    add_to_queue(new_posts)
    print("черновиков добавлено:", made)
    return 0


ORCH_SYSTEM = """Ты — оркестратор контентной сети. Дан seed (идея) и каналы сети.
Определи, как сеть использует идею: primary_channel, secondary_channels, excluded_channels,
adaptations (для каждого канала: format, hook, value, cta, visual), reasoning.
Правила: один seed не обязан идти во все каналы; одинаковый текст — не адаптация
(у каждой версии свой hook, формат, ценность, CTA); не заполняй план ради плана.
Верни строго JSON: {"seed_id": "...", "decision": "use|skip", "primary_channel": "...",
"secondary_channels": [], "excluded_channels": [],
"adaptations": [{"channel": "...", "format": "...", "hook": "...", "value": "...", "cta": "...", "visual": "..."}],
"reasoning": []}"""


def cmd_seed(cfg, idea):
    """Seed → оркестратор → адаптации под каналы (одна идея, разные продукты)."""
    conn = db()
    queue = load_json(QUEUE, [])
    history = load_history()
    chans = load_channel_configs()
    lines = []
    for key, ch in chans.items():
        c = ch.get("channel") or ch
        lines.append("- %s «%s» (%s): %s | рубрики: %s" % (
            key, c.get("name"), ch.get("role"), ch.get("positioning"), ", ".join(ch.get("rubrics") or [])))
    try:
        plan_raw = bridge_chat(cfg, ORCH_SYSTEM, "Seed (идея): %s\n\nКаналы сети:\n%s" % (idea, "\n".join(lines)))
    except Exception as exc:
        print("оркестратор недоступен: %s" % str(exc)[:80])
        return 1
    plan = parse_json_reply(plan_raw)
    if not plan or not plan.get("adaptations"):
        print("оркестратор не дал план")
        return 1
    print("seed:", plan.get("seed_id"), "| primary:", plan.get("primary_channel"),
          "| secondary:", plan.get("secondary_channels"))
    new_posts = []
    made = 0
    for ad in plan["adaptations"]:
        channel = ad.get("channel")
        if channel not in chans:
            continue
        system = build_system_prompt(channel)
        user_msg = ("Seed: %s\nАдаптация: формат=%s; hook=%s; ценность=%s; CTA=%s; визуал=%s\n"
                    "Сделай ОТДЕЛЬНЫЙ пост под этот канал (не копию других каналов)." % (
                        idea, ad.get("format"), ad.get("hook"), ad.get("value"), ad.get("cta"), ad.get("visual")))
        try:
            reply = bridge_chat(cfg, system, user_msg)
        except Exception as exc:
            print("✗ %s: %s" % (channel, str(exc)[:60]))
            continue
        draft = parse_json_reply(reply)
        if not draft or draft.get("skip") or looks_garbage(draft.get("text", "")):
            print("· пропуск (%s)" % channel)
            continue
        sh = simhash(draft.get("text", ""))
        if any(hamming(sh, qh) <= 10 for qh in [simhash(p.get("text", "")) for p in queue]):
            print("· повтор (%s)" % channel)
            continue
        qc = quality_check(cfg, draft.get("text", ""), history)
        if (qc.get("status") or "PASS").upper() != "PASS":
            print("· QC %s (%s): %s" % (qc.get("status"), channel, (qc.get("reason") or "")[:60]))
            continue
        when = (datetime.now(MSK) + timedelta(minutes=2)).replace(second=0, microsecond=0)
        pid = "sd-%s-%s-%s" % (when.strftime("%Y-%m-%d"), when.strftime("%H%M%S"), channel[:2])
        new_posts.append({
            "id": pid, "channel": channel, "scheduled_at": when.isoformat(),
            "rubric": draft.get("rubric") or "Секрет дня", "text": draft.get("text", "").strip(),
            "hashtags": draft.get("hashtags") or [], "image": "", "source_image": "",
            "status": post_status(channel), "check": {"evergreen": True, "editor": "seed"},
            "quality": {k: qc.get(k) for k in ("value", "originality", "clarity", "actionability",
                                               "hook", "saveability", "shareability", "naturalness",
                                               "repetition", "risk")},
            "content_dna": qc.get("content_dna") or {},
            "origin": "seed",
        })
        made += 1
        print("✓ %s ← seed | QC value=%s save=%s" % (pid, qc.get("value"), qc.get("saveability")))
    add_to_queue(new_posts)
    print("адаптаций создано:", made)
    return 0


def cmd_release(cfg, count, channel):
    """Выпускает N постов из библиотеки в утверждение (пачками)."""
    queue = load_json(QUEUE, [])
    released = 0
    for post in queue:
        if released >= count:
            break
        if post.get("channel") == channel and post.get("status") == "library":
            post["status"] = "pending"
            post["_sent"] = False
            released += 1
    save_json(QUEUE, queue)
    left = sum(1 for p in queue if p.get("channel") == channel and p.get("status") == "library")
    print("выпущено в утверждение: %d | осталось в библиотеке: %d" % (released, left))
    return 0


def cmd_digest(cfg, count, channel):
    """Собирает подборку инструментов из свежих материалов (github/hn)."""
    conn = db()
    rows = conn.execute(
        "SELECT id, source, text FROM items WHERE used = 0 AND dupe = 0 "
        "AND source = 'github_trending' ORDER BY id DESC LIMIT 40").fetchall()
    if len(rows) < 5:
        rows += conn.execute(
            "SELECT id, source, text FROM items WHERE used = 0 AND dupe = 0 "
            "AND source = 'hacker_news' ORDER BY id DESC LIMIT 20").fetchall()
    if len(rows) < 5:
        print("мало материалов для подборки (нужно 5+, есть %d) — сначала --ingest" % len(rows))
        return 1
    materials = "\n".join("- %s" % r[2] for r in rows[:count])
    system = ("Ты редактор Telegram-канала «Нейро-секреты». Собери из материалов подборку инструментов "
              "в формате:\n⚡️ Подборка <тема>: <что внутри>\n\n"
              "⚡️ <Инструмент> — <что делает, одной строкой>\n(5–12 пунктов)\n\n"
              "Сохраняйте, чтобы не искать потом 📌\n\n"
              "Для GitHub-инструментов указывай ссылку в конце строки: (github.com/owner/repo). "
              "Для остальных — ссылку из материала, если есть.\n"
              "Пиши по-русски, просто, без воды. Верни JSON: "
              "{\"text\": \"...\", \"hashtags\": [\"#подборка\", \"#нейросети\"], \"rubric\": \"Подборка\"}")
    try:
        reply = bridge_chat(cfg, system, "Материалы:\n%s" % materials)
    except Exception as exc:
        print("✗ подборка: %s: %s" % (type(exc).__name__, str(exc)[:80]))
        return 1
    draft = parse_json_reply(reply)
    if not draft or not draft.get("text"):
        print("✗ подборка: не удалось собрать")
        return 1
    when = (datetime.now(MSK) + timedelta(minutes=1)).replace(microsecond=0)
    post_id = "dg-%s-%s" % (when.strftime("%Y-%m-%d"), when.strftime("%H%M%S"))
    new_posts = [{
        "id": post_id, "channel": channel, "scheduled_at": when.isoformat(),
        "rubric": draft.get("rubric") or "Подборка", "text": draft["text"].strip(),
        "hashtags": draft.get("hashtags") or ["#подборка", "#нейросети"],
        "image": "", "source_image": "", "status": "pending",
        "check": {"evergreen": True, "editor": "digest"}, "origin": "github/hn",
    }]
    for r in rows[:count]:
        conn.execute("UPDATE items SET used = 1 WHERE id = ?", (r[0],))
    conn.commit()
    add_to_queue(new_posts)
    print("подборка создана:", post_id)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Конвейер контента")
    ap.add_argument("--ingest", action="store_true", help="собрать посты из источников")
    ap.add_argument("--adapt", type=int, metavar="N", help="адаптировать N постов в очередь")
    ap.add_argument("--stats", action="store_true", help="статистика базы")
    ap.add_argument("--channel", default="neuro_secrets", help="канал для адаптации")
    ap.add_argument("--digest", type=int, metavar="N", help="собрать подборку инструментов из N материалов")
    ap.add_argument("--seed", metavar="IDEA", help="seed-идея → адаптации под каналы сети")
    ap.add_argument("--release", type=int, metavar="N", help="выпустить N постов из библиотеки в утверждение")
    ap.add_argument("--auto", action="store_true", help="модель сама выбирает канал")
    args = ap.parse_args()
    cfg = load_config()
    if args.ingest:
        return cmd_ingest(cfg)
    if args.stats:
        return cmd_stats(cfg)
    if args.adapt:
        return cmd_adapt(cfg, args.adapt, args.channel, auto=args.auto)
    if args.digest:
        return cmd_digest(cfg, args.digest, args.channel)
    if args.seed:
        return cmd_seed(cfg, args.seed)
    if args.release:
        return cmd_release(cfg, args.release, args.channel)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
