#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""news_conveyor.py — новостной конвейер: свежие новости ИИ → канал «Новости ИИ».

Схема:
  1. --ingest   : тянет новости из зарубежных (приоритет) и местных источников
                  (Google News RSS + Telegram-каналы), фильтр свежести ≤ N часов
  2. --clusters : группирует похожие новости и отмечает подтверждённые (2+ источника)
  3. --adapt N  : переводит/переписывает подтверждённые новости и ставит в очередь
                  на публикацию «сейчас» (status approved, scheduled_at = now+2 мин)
  4. --stats    : что собрано

Примеры:
    python3 news_conveyor.py --ingest
    python3 news_conveyor.py --clusters
    python3 news_conveyor.py --adapt 2
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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conveyor import (fetch, fetch_bytes, parse_tme, simhash, hamming, to_signed, from_signed,
                      bridge_chat, parse_json_reply, looks_garbage)

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CONFIG = os.path.join(HERE, "news_sources.json")
QUEUE = os.path.join(HERE, "queue.json")
MSK = ZoneInfo("Europe/Moscow")

PROMPTS_DIR = os.path.join(HERE, "prompts")
AI_SCOPE = ("\n\nДОПОЛНИТЕЛЬНОЕ УСЛОВИЕ КАНАЛА: канал только про ИИ/нейросети и технологии ИИ. "
            "Если событие не относится к ИИ — верни REJECT (NOT_NEWS).")
GENERATOR_CONTRACT = ("\n\n---\n\nТВОЯ ЗАДАЧА СЕЙЧАС: только ПРОВЕРИТЬ материал и вернуть вердикт. "
                      "Текст новости НЕ пиши. Ответ — ровно ОДИН JSON-объект, без markdown и пояснений:\n"
                      '{"status": "READY", "news_type": "product_release", "headline": "...", '
                      '"lead": "...", "availability": "...", "price": "...", "limitations": "...", '
                      '"event_date": "...", "source_count": N, "news_score": N}\n'
                      "READY — если есть конкретное СОБЫТИЕ: релиз, обновление, новая функция, изменение "
                      "цены или лимитов, сделка с условиями, результат теста или исследования, решение "
                      "регулятора, сбой/восстановление, открытие доступа. Достаточно одного подтверждённого "
                      "факта об изменении (что именно вышло/изменилось и для кого); цифры и детали — плюс, "
                      "но их отсутствие не повод для отказа. Единственный источник — нормально, если это не слух.\n"
                      "REJECT — мнения и интервью, обзоры «о чём говорят», прогнозы и «как далеко зайдёт», "
                      "слухи и переговоры без подтверждения, кликбейт и сенсации без конкретики, дайджесты, "
                      "пересказ старых событий, материал не про ИИ:\n"
                      '{"status": "REJECT", "reason": "NOT_NEWS"}\n'
                      "NEEDS_VERIFICATION — событие похоже на реальное, но подтверждено только одним "
                      "сообщением без деталей:\n"
                      '{"status": "NEEDS_VERIFICATION", "reason": "INSUFFICIENT_EVIDENCE"}')
NO_FILLER = ("\n\nСТРОГО ЗАПРЕЩЕНО писать о том, чего нет в источнике: «детали не раскрыты», "
             "«подробности не раскрываются», «пока не известно», «неизвестно», «не уточняется», "
             "«не сообщается», «оценки не раскрыты», «детали недоступны», «сумма не раскрыта», "
             "«но есть нюанс» и любые похожие фразы. Не упоминай отсутствие информации вообще. "
             "Ограничение из источника (beta, регион, только API, бенчмарк компании) — одной короткой "
             "строкой в конце, без заголовка. Если конкретики мало — короткий пост 400–700 знаков "
             "только из подтверждённых фактов; никогда не растягивай пост.")
WRITER_CONTRACT = ("\n\n---\n\nФОРМАТ ОТВЕТА — строго JSON без пояснений и markdown:\n"
                   '{"form": "short|release|compare|research|deal", "title": "заголовок", '
                   '"text": "готовый пост", "hashtags": ["#новости_ии"], '
                   '"image_prompt": "описание картинки по-русски", "image_prompt_en": "same in english"}'
                   + NO_FILLER)
FILLER_RE = re.compile(r"не раскрыва|не раскрыт|неизвестн|не известн|не уточня|"
                       r"детали недоступн|подробности не|оценки не|не сообща|не указан|"
                       r"не называ|не комментир|но есть нюанс", re.I)
WRITER_CONTRACT = ("\n\n---\n\nФОРМАТ ОТВЕТА — строго JSON без пояснений и markdown:\n"
                   '{"form": "short|release|compare|research|deal", "title": "заголовок", '
                   '"text": "готовый пост", "hashtags": ["#новости_ии"], '
                   '"image_prompt": "описание картинки по-русски", "image_prompt_en": "same in english"}')
RUBRIC_BY_FORM = {"short": "Новость", "release": "Релиз", "compare": "Сравнение",
                  "research": "Исследование", "deal": "Сделка",
                  "digest": "Дайджест", "story": "Новость", "razbor": "Разбор"}
FALLBACK_GENERATOR = ("Ты — фильтр новостей про ИИ. Верни строго JSON: "
                      '{"status": "READY", "news_type": "...", "headline": "...", "event_date": "...", '
                      '"news_score": 0} если в источнике есть конкретное событие (релиз, функция, цена, '
                      'сделка, тест, сбой, решение регулятора), иначе {"status": "REJECT", "reason": "NOT_NEWS"}.')
FALLBACK_WRITER = ("Ты — автор Telegram-новостей про ИИ. Пиши плотно, только факты из исходника, "
                   "без выводов и воды.")


def load_prompt(name, default=""):
    try:
        with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return default


def filler_hit(text):
    m = FILLER_RE.search(text or "")
    return m.group(0) if m else ""


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


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, lang TEXT, title TEXT, text TEXT, url TEXT, ts TEXT,
        image_url TEXT, hash TEXT UNIQUE, simhash INTEGER DEFAULT 0,
        cluster INTEGER DEFAULT 0, used INTEGER DEFAULT 0)""")
    conn.commit()
    return conn


def parse_rss(xml_text, source, lang):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        raw_desc = (item.findtext("description") or "")
        full = ""
        for child in item:
            if child.tag.endswith("encoded") and (child.text or ""):
                full = re.sub(r"<[^>]+>", " ", child.text)
                break
        desc = html_mod.unescape(re.sub(r"<[^>]+>", "", raw_desc)).strip()
        if full:
            desc = (desc + " " + html_mod.unescape(re.sub(r"\s+", " ", full)).strip())[:6000]
        pub = (item.findtext("pubDate") or "").strip()
        try:
            ts = parsedate_to_datetime(pub).astimezone(MSK)
        except Exception:
            continue
        items.append({
            "source": source, "lang": lang, "title": title,
            "text": (title + ". " + desc)[:1200], "url": link,
            "ts": ts.isoformat(), "image_url": "",
        })
    return items


WEAK_DRAFT_MARKERS = ("не выпущена", "не выпущен", "не раскрыва", "держат в секрете",
                      "название неизвестно", "название держат", "слух", "в разработке",
                      "планирует выпустить", "подробности неизвестны", "детали держат",
                      "независимой модели", "может появиться")


def weak_draft(text):
    low = (text or "").lower()
    return any(m in low for m in WEAK_DRAFT_MARKERS)


WEAK_TITLE_MARKERS = ("rumor", "rumour", "reportedly", "unreleased", "secret", "leak",
                      "allegedly", "in development", "слух", "якобы", "секрет", "в разработке",
                      "планирует", "не выпущена", "не выпущен", "может появиться")


def weak_item(title, text):
    """Предфильтр: слухи, «в разработке», без конкретики."""
    low = ((title or "") + " " + (text or ""))[:300].lower()
    return any(m in low for m in WEAK_TITLE_MARKERS)


def age_hours(ts):
    try:
        when = datetime.fromisoformat(ts)
    except Exception:
        return 999
    if when.tzinfo is None:
        when = when.replace(tzinfo=MSK)
    return (datetime.now(MSK) - when).total_seconds() / 3600.0


def cmd_ingest(cfg):
    conn = db()
    max_age = cfg.get("max_age_hours", 12)
    proxy = cfg.get("proxy")
    new = total = 0
    for src in cfg.get("rss", []):
        try:
            xml_text = fetch(src["url"], proxy, timeout=40)
        except Exception as exc:
            print("✗ %-22s %s: %s" % (src["name"], type(exc).__name__, str(exc)[:50]))
            continue
        items = [it for it in parse_rss(xml_text, src["name"], src.get("lang", "en"))
                 if age_hours(it["ts"]) <= max_age and not weak_item(it["title"], it["text"])]
        for it in items:
            h = hashlib.md5((it["title"] + it["url"]).encode("utf-8")).hexdigest()
            if conn.execute("SELECT 1 FROM news WHERE hash = ?", (h,)).fetchone():
                continue
            sh = simhash(it["text"])
            conn.execute("INSERT OR IGNORE INTO news (source, lang, title, text, url, ts, image_url, hash, simhash) "
                         "VALUES (?,?,?,?,?,?,?,?,?)",
                         (it["source"], it["lang"], it["title"], it["text"], it["url"], it["ts"],
                          it["image_url"], h, to_signed(sh)))
            new += 1
        total += len(items)
        print("✓ %-22s свежих %-3d" % (src["name"], len(items)))
        time.sleep(0.5)
    for src in cfg.get("tg", []):
        name = src["name"]
        try:
            page = fetch("https://t.me/s/%s" % urllib.parse.quote(name), proxy, timeout=30)
        except Exception as exc:
            print("✗ %-22s %s: %s" % (name, type(exc).__name__, str(exc)[:50]))
            continue
        items = [it for it in parse_tme(page, name) if age_hours(it["ts"]) <= max_age]
        for it in items:
            h = hashlib.md5(it["text"][:400].encode("utf-8")).hexdigest()
            if conn.execute("SELECT 1 FROM news WHERE hash = ?", (h,)).fetchone():
                continue
            sh = simhash(it["text"])
            conn.execute("INSERT OR IGNORE INTO news (source, lang, title, text, url, ts, image_url, hash, simhash) "
                         "VALUES (?,?,?,?,?,?,?,?,?)",
                         (name, src.get("lang", "ru"), it["text"][:120].replace("\n", " "), it["text"],
                          "https://t.me/%s/%s" % (name, it["post_id"].split("/")[-1]), it["ts"],
                          it["image_url"], h, to_signed(sh)))
            new += 1
        total += len(items)
        print("✓ %-22s свежих %-3d" % (name, len(items)))
        time.sleep(0.5)
    conn.commit()
    print("итого свежих: %d, новых: %d (окно %dч)" % (total, new, max_age))
    return 0


ENTITIES = ("openai", "anthropic", "google", "deepmind", "meta", "microsoft", "nvidia",
            "apple", "amazon", "gpt", "chatgpt", "claude", "gemini", "llama", "grok",
            "midjourney", "sora", "qwen", "deepseek", "mistral", "runway", "xai",
            "tesla", "stability", "openrouter", "copilot", "perplexity")


STOP_TERMS = {
    # русские общежанровые (не считаем их признаком одного сюжета)
    "модель", "модели", "моделью", "моделей", "версия", "версии", "версию", "версий",
    "цена", "цены", "цену", "ценам", "ценах", "токен", "токены", "токенов", "токена",
    "токенам", "контекст", "контекста", "миллион", "миллиона", "миллионов",
    "доступ", "доступна", "доступен", "доступно", "доступных", "тест", "теста", "тесте",
    "тесты", "тестов", "тестирование", "против", "компания", "компании", "компанию",
    "компаний", "компаниям", "пользователь", "пользователи", "пользователей",
    "пользователям", "пользователя", "разработчик", "разработчики", "разработчиков",
    "разработчикам", "релиз", "релиза", "релизе", "релизу", "релизы",
    "обновление", "обновления", "обновлении", "функция", "функции", "функцию", "функций",
    "продукт", "продукта", "продукте", "продукты", "рынок", "рынка", "рынке",
    "бесплатный", "бесплатно", "бесплатных", "первый", "первая", "первых", "первым",
    "новый", "новая", "новое", "новые", "новых", "нового", "новой",
    "только", "через", "после", "более", "менее", "также", "чтобы", "будет", "могут",
    "может", "теперь", "сегодня", "впервые", "всего", "около", "самый", "самая",
    "вышла", "вышел", "выпустила", "выпустил", "запустила", "запустил", "открыла",
    "открыл", "добавила", "добавил", "снизила", "снизил", "получила", "получил",
    "стала", "стал", "объявила", "объявил", "набрала", "набрал", "представила",
    "представил", "представили",
    # английские общежанровые
    "model", "models", "version", "versions", "price", "prices", "pricing", "token",
    "tokens", "context", "million", "release", "released", "releases", "available",
    "user", "users", "developer", "developers", "company", "companies", "test", "tests",
    "tested", "benchmark", "new", "more", "less", "with", "that", "this", "from",
    "after", "will", "have", "than", "into", "over", "about", "their", "they", "been",
    "were", "which", "while", "launch", "launches", "launched", "update", "updates",
    "updated", "feature", "features", "product", "products", "platform", "using",
    "announced", "introduces", "introduced", "unveils", "unveiled", "adds", "added",
}


def key_terms(text):
    low = (text or "").lower()
    terms = {w for w in re.findall(r"[a-zа-яё0-9]{4,}", low)
             if not w.isdigit() and w not in STOP_TERMS}
    ents = {e for e in ENTITIES if e in low}
    return terms, ents


def same_story(a, b):
    """Одна и та же новость? Сущности + пересечение терминов."""
    a_terms, a_ents = a
    b_terms, b_ents = b
    if a_ents and b_ents:
        return len(a_ents & b_ents) >= 1 and len(a_terms & b_terms) >= 3
    return len(a_terms & b_terms) >= 6


def cmd_clusters(cfg):
    """Сверяет новые айтемы со ВСЕМИ свежими кластерами (в т.ч. обработанными).

    Если сюжет уже обработан (все айтемы кластера used=1) — новый айтем
    поглощается (used=1), пост по нему не создаётся. Это защита от дублей,
    когда один и тот же сюжет приходит снова через несколько заходов ingest.
    """
    conn = db()
    cutoff = (datetime.now(MSK) - timedelta(hours=72)).isoformat()
    # legacy-чистка: необработанные айтемы старых кластеров пересобираем заново
    # (старый баг переиспользовал id кластеров → в одном кластере разные сюжеты)
    conn.execute("UPDATE news SET cluster = 0 WHERE cluster != 0 AND used = 0 AND ts > ?", (cutoff,))
    conn.commit()
    # кластеры, по которым уже есть пост (не отклонённый): их сюжеты считаем «живыми» и поглощаем повторы
    posted_clusters = set()
    for p in (load_json(QUEUE, []) or []):
        if p.get("channel") != "ai_news" or p.get("status") == "rejected":
            continue
        m = re.search(r"-c(\d+)$", p.get("id") or "")
        if m:
            posted_clusters.add(int(m.group(1)))
    old_rows = conn.execute(
        "SELECT cluster, title, text, used FROM news WHERE cluster != 0 AND ts > ?",
        (cutoff,)).fetchall()
    old_clusters = {}
    for cid, title, text, used in old_rows:
        cl = old_clusters.setdefault(cid, {"keys": [], "all_used": True})
        cl["keys"].append(key_terms((title or "") + " " + (text or "")))
        if not used:
            cl["all_used"] = False
    rows = conn.execute("SELECT id, source, title, text, ts FROM news WHERE cluster = 0 "
                        "ORDER BY ts DESC").fetchall()
    new_clusters = []
    next_id = (max(old_clusters) + 1) if old_clusters else 1
    absorbed = 0
    for item_id, source, title, text, ts in rows:
        keys = key_terms((title or "") + " " + (text or ""))
        target = None
        absorb = False
        for cid, cl in old_clusters.items():
            if cl["all_used"] and cid not in posted_clusters:
                continue  # сюжет отклонён/не дошёл до поста — даём шанс свежему покрытию
            if any(same_story(keys, k) for k in cl["keys"]):
                target = cid
                absorb = cl["all_used"]
                break
        if target is not None:
            if not absorb:
                old_clusters[target]["keys"].append(keys)
        else:
            for ncl in new_clusters:
                if any(same_story(keys, k) for k in ncl["keys"]):
                    ncl["keys"].append(keys)
                    target = ncl["id"]
                    break
            if target is None:
                target = next_id
                next_id += 1
                new_clusters.append({"id": target, "keys": [keys]})
        if absorb:
            absorbed += 1
            conn.execute("UPDATE news SET cluster = ?, used = 1 WHERE id = ?", (target, item_id))
        else:
            conn.execute("UPDATE news SET cluster = ? WHERE id = ?", (target, item_id))
    conn.commit()
    print("новых кластеров: %d | поглощено дублей: %d | обработано айтемов: %d" % (
        len(new_clusters), absorbed, len(rows)))
    return 0


def cmd_stats(cfg):
    conn = db()
    rows = conn.execute("SELECT source, COUNT(*) FROM news GROUP BY source ORDER BY COUNT(*) DESC").fetchall()
    for source, cnt in rows:
        print("%-22s %4d" % (source, cnt))
    total = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    fresh = conn.execute("SELECT COUNT(*) FROM news WHERE ts > ?",
                         ((datetime.now(MSK) - timedelta(hours=cfg.get("max_age_hours", 12))).isoformat(),)).fetchone()[0]
    print("---")
    print("всего: %d | свежих: %d" % (total, fresh))
    return 0


def fetch_article_text(url, proxy, limit=3500):
    """Тянет текст статьи (абзацы) — чтобы у модели были факты, а не сниппет."""
    if not url or "news.google.com" in url:
        return ""
    try:
        page = fetch(url, proxy, timeout=30)
    except Exception:
        return ""
    page = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", page, flags=re.S | re.I)
    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)
    text = " ".join(re.sub(r"<[^>]+>", " ", p) for p in paras)
    text = html_mod.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def news_image(cfg, item_url, image_prompt_en, post_id, proxy):
    """Картинка для новости: og:image статьи (кроме Google News и мелких) → генерация."""
    os.makedirs(os.path.join(HERE, "posts"), exist_ok=True)
    path = "posts/%s.jpg" % post_id
    full = os.path.join(HERE, path)
    if item_url and "news.google.com" not in item_url:
        try:
            page = fetch(item_url, proxy, timeout=30)
            m = (re.search(r'property="og:image"[^>]*content="([^"]+)"', page)
                 or re.search(r'content="([^"]+)"[^>]*property="og:image"', page))
            if m and m.group(1).startswith("http"):
                blob = fetch_bytes(m.group(1), proxy)
                if len(blob) > 5000:
                    from PIL import Image
                    import io
                    image = Image.open(io.BytesIO(blob)).convert("RGB")
                    if min(image.width, image.height) >= 600:  # мелкие (логотипы) не берём
                        image.save(full, "JPEG", quality=92)
                        return path
        except Exception:
            pass
    if image_prompt_en:
        try:
            url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(image_prompt_en)
                   + "?width=1024&height=768&nologo=true")
            blob = fetch_bytes(url, proxy, timeout=180)
            if len(blob) > 5000:
                from PIL import Image
                import io
                image = Image.open(io.BytesIO(blob)).convert("RGB")
                image = image.crop((0, 0, image.width, int(image.height * 0.92)))
                image.save(full, "JPEG", quality=92)
                return path
        except Exception:
            pass
    return ""


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


def cmd_adapt(cfg, count):
    """Собирает посты по КЛАСТЕРАМ (событиям): несколько источников → один полный пост."""
    conn = db()
    queue = load_json(QUEUE, [])
    max_age = cfg.get("max_age_hours", 12)
    gen_prompt = load_prompt("NEWS_GENERATOR.md", FALLBACK_GENERATOR)
    writer_prompt = load_prompt("NEWS_WRITER_STYLE.md", FALLBACK_WRITER)
    news_hashes = [simhash(p.get("text", "")) for p in queue if p.get("channel") == "ai_news"]
    clusters = conn.execute(
        "SELECT cluster, MAX(ts) AS last FROM news WHERE used = 0 AND cluster != 0 "
        "GROUP BY cluster ORDER BY last DESC LIMIT 25").fetchall()
    new_posts = []
    made = 0
    for cluster_id, last_ts in clusters:
        if made >= count:
            break
        items = conn.execute(
            "SELECT id, source, title, text, url, image_url FROM news WHERE cluster = ? AND used = 0 "
            "ORDER BY LENGTH(text) DESC LIMIT 6", (cluster_id,)).fetchall()
        if not items:
            continue
        # свежесть события
        if age_hours(last_ts) > max_age:
            for it in items:
                conn.execute("UPDATE news SET used = 1 WHERE id = ?", (it[0],))
            conn.commit()
            continue
        merged = "\n\n".join("Источник %s: %s" % (it[1], (it[3] or "")[:1800]) for it in items)
        # обогащение: если сниппеты короткие — тянем полный текст статьи (фактов будет больше)
        extra = []
        for it in items[:2]:
            u = it[4] or ""
            if u and "news.google.com" not in u and len(it[3] or "") < 900:
                art = fetch_article_text(u, cfg.get("proxy"), 2500)
                if art and len(art) > 500:
                    extra.append("Полный текст (%s): %s" % (it[1], art))
        if extra:
            merged += "\n\n" + "\n\n".join(extra)
            print("· подтянут полный текст статей: %d" % len(extra))
        sh = simhash(merged)
        if any(hamming(sh, qh) <= 10 for qh in news_hashes):
            for it in items:
                conn.execute("UPDATE news SET used = 1 WHERE id = ?", (it[0],))
            conn.commit()
            print("· событие уже было (кластер %s)" % cluster_id)
            continue
        # Этап 1: NEWS_GENERATOR — это вообще новость? (конкретное событие или REJECT)
        try:
            stage1 = bridge_chat(cfg, gen_prompt + AI_SCOPE + GENERATOR_CONTRACT,
                                 "Проверь источник. Ответ — только JSON (READY или REJECT или "
                                 "NEEDS_VERIFICATION), текст новости НЕ пиши.\n\n"
                                 "Источник(и) (сообщений: %d):\n\n%s" % (len(items), merged[:12000]))
        except Exception as exc:
            print("✗ фильтр: %s: %s" % (type(exc).__name__, str(exc)[:70]))
            continue
        event = parse_json_reply(stage1)
        status = ((event or {}).get("status") or "").upper()
        if not event or status != "READY":
            for it in items:
                conn.execute("UPDATE news SET used = 1 WHERE id = ?", (it[0],))
            conn.commit()
            reason = (event or {}).get("reason") or ("не JSON" if not event else status)
            print("· отсев (%s): кластер %s" % (reason, cluster_id))
            continue
        # Этап 2: NEWS_WRITER_STYLE — пишем пост по проверенному событию
        try:
            stage2 = bridge_chat(cfg, writer_prompt + WRITER_CONTRACT,
                                 "EVENT (проверенное событие):\n%s\n\nИСХОДНЫЕ ФАКТЫ:\n%s"
                                 % (json.dumps(event, ensure_ascii=False)[:4000], merged[:9000]))
        except Exception as exc:
            print("✗ писатель: %s: %s" % (type(exc).__name__, str(exc)[:70]))
            continue
        draft = parse_json_reply(stage2)
        for it in items:
            conn.execute("UPDATE news SET used = 1 WHERE id = ?", (it[0],))
        conn.commit()
        if not draft or draft.get("skip"):
            print("· пропуск (кластер %s): писатель не дал пост" % cluster_id)
            continue
        if looks_garbage(draft.get("text", "")):
            print("✗ мусор/иероглифы (кластер %s)" % cluster_id)
            continue
        fill = filler_hit(draft.get("text", ""))
        if fill:
            # одна попытка переписать без фраз об отсутствии информации
            try:
                stage2b = bridge_chat(cfg, writer_prompt + WRITER_CONTRACT,
                                     "EVENT (проверенное событие):\n%s\n\nИСХОДНЫЕ ФАКТЫ:\n%s\n\n"
                                     "В предыдущем варианте были запрещённые фразы («%s») — перепиши "
                                     "без них и без любых упоминаний отсутствующей информации."
                                     % (json.dumps(event, ensure_ascii=False)[:2000], merged[:4000], fill))
                draft2 = parse_json_reply(stage2b)
            except Exception:
                draft2 = None
            text2 = (draft2 or {}).get("text", "")
            if text2 and not filler_hit(text2) and not looks_garbage(text2):
                draft = draft2
            else:
                print("· филлер не убран (%s, кластер %s) — пропуск" % (fill, cluster_id))
                continue
        news_hashes.append(simhash(draft.get("text", "")))
        when = (datetime.now(MSK) + timedelta(minutes=2)).replace(second=0, microsecond=0)
        post_id = "an-%s-%s-c%s" % (when.strftime("%Y-%m-%d"), when.strftime("%H%M%S"), cluster_id)
        image_path = ""
        first = items[0]
        if first[5]:
            try:
                blob = fetch_bytes(first[5], cfg.get("proxy"))
                if len(blob) > 3000:
                    os.makedirs(os.path.join(HERE, "posts"), exist_ok=True)
                    image_path = "posts/%s.jpg" % post_id
                    with open(os.path.join(HERE, image_path), "wb") as fh:
                        fh.write(blob)
            except Exception:
                image_path = ""
        if not image_path:
            image_path = news_image(cfg, first[4], draft.get("image_prompt_en"), post_id, cfg.get("proxy"))
        src_url = ""
        for it in items:
            u = it[4] or ""
            if u and "news.google.com" not in u:
                src_url = u
                break
        if not src_url:
            src_url = items[0][4] or ""
        form = (draft.get("form") or draft.get("format") or "").strip().lower()
        new_posts.append({
            "id": post_id, "channel": "ai_news", "scheduled_at": when.isoformat(),
            "rubric": RUBRIC_BY_FORM.get(form, "Новость"),
            "news_type": event.get("news_type") or "",
            "news_score": event.get("news_score"),
            "event_date": event.get("event_date") or "",
            "text": draft.get("text", "").strip(),
            "hashtags": draft.get("hashtags") or ["#новости_ии"],
            "image": image_path, "source_image": first[5] or "",
            "source_url": src_url,
            "status": "pending", "check": {"news": True, "sources": len(items)},
            "origin": ", ".join(sorted({it[1] for it in items}))[:80],
        })
        made += 1
        print("✓ %s ← кластер %s (%d источников, %d симв.)" % (
            post_id, cluster_id, len(items), len(draft.get("text", ""))))
    add_to_queue(new_posts)
    print("новостей добавлено:", made)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Новостной конвейер")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--clusters", action="store_true")
    ap.add_argument("--adapt", type=int, metavar="N")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    cfg = load_json(CONFIG, {})
    if args.ingest:
        return cmd_ingest(cfg)
    if args.clusters:
        return cmd_clusters(cfg)
    if args.stats:
        return cmd_stats(cfg)
    if args.adapt:
        return cmd_adapt(cfg, args.adapt)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
