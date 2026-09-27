#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_channel.py — userbot: забирает посты канала (в т.ч. закрытого) в базы конвейера.

Использование:
    venv/bin/python fetch_channel.py --list
    venv/bin/python fetch_channel.py <@username | t.me/+invite | id | часть названия> [--count 200] [--no-news]
"""
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo

import socks
from telethon import TelegramClient
try:
    from telethon.extensions import html as tl_html
except Exception:
    tl_html = None

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY = (socks.SOCKS5, "127.0.0.1", 1080)
CH = os.path.expanduser("~/channels")
sys.path.insert(0, CH)
from conveyor import MEDIA, simhash, to_signed  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")


def content_db():
    conn = sqlite3.connect(os.path.join(CH, "content.db"), timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, post_id TEXT, text TEXT, ts TEXT, views INTEGER,
        image_url TEXT, image_local TEXT DEFAULT '', used INTEGER DEFAULT 0,
        hash TEXT UNIQUE, simhash INTEGER DEFAULT 0, dupe INTEGER DEFAULT 0)""")
    try:
        conn.execute("ALTER TABLE items ADD COLUMN video_local TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    for col, decl in (("media_group_id", "TEXT DEFAULT ''"),
                      ("media_seq", "INTEGER DEFAULT 0"),
                      ("is_media", "INTEGER DEFAULT 0")):
        try:
            conn.execute("ALTER TABLE items ADD COLUMN %s %s" % (col, decl))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def news_db():
    conn = sqlite3.connect(os.path.join(CH, "news.db"), timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, lang TEXT, title TEXT, text TEXT, url TEXT, ts TEXT,
        image_url TEXT, hash TEXT UNIQUE, simhash INTEGER DEFAULT 0,
        cluster INTEGER DEFAULT 0, used INTEGER DEFAULT 0)""")
    conn.commit()
    return conn


async def resolve(client, ref):
    try:
        return await client.get_entity(ref)
    except Exception:
        pass
    r = str(ref).strip()
    if r.lstrip("-").isdigit():
        try:
            return await client.get_entity(int(r))
        except Exception:
            pass
    needle = r.lower().replace("https://t.me/", "").replace("http://t.me/", "").lstrip("+@").strip("/")
    async for d in client.iter_dialogs():
        ent = d.entity
        title = (getattr(ent, "title", "") or "").lower()
        uname = (getattr(ent, "username", "") or "").lower()
        eid = str(ent.id)
        if needle and (needle in title or needle == uname or needle == eid or needle == ("-100" + eid)):
            return ent
    return None


SELF_REF = re.compile(r"aimarketcap", re.I)


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text):
    """Выкидывает HTML-разметку, которую возвращает tl_html.unparse.

    Без этого текст одного и того же материала из Telegram и из VK/VK-клипа
    даёт разный md5 (теги есть только в телеграмной версии) — и копия
    проходит дедуп как новая.
    """
    if not text:
        return ""
    t = _TAG_RE.sub(" ", text)
    for a, b in (("&nbsp;", " "), ("&quot;", '"'), ("&#39;", "'"),
                 ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
        t = t.replace(a, b)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


async def grab_media(client, msg, name, post_id, is_video):
    """Скачивает фото/видео в media/, возвращает относительный путь или ''."""
    ext = ".mp4" if is_video else ".jpg"
    floor = 10000 if is_video else 3000
    fname = re.sub(r"[^\w.-]", "_", "%s-%s" % (name, post_id))[:90] + ext
    dest = os.path.join(MEDIA, fname)
    try:
        if not (os.path.exists(dest) and os.path.getsize(dest) > floor):
            os.makedirs(MEDIA, exist_ok=True)
            await client.download_media(msg, file=dest)
        if os.path.exists(dest) and os.path.getsize(dest) > floor:
            return "media/" + fname
    except Exception:
        pass
    return ""


def scrub_selfref(text, is_title=False):
    """Убирает самоссылки источника (AIMarketCap): строки-рекламы и упоминания."""
    if is_title:
        t = SELF_REF.sub("", text or "").strip(" ·—–-|🔗\t")
        return t if len(t) >= 4 else ""
    keep = [ln for ln in (text or "").split("\n") if not SELF_REF.search(ln)]
    return "\n".join(keep)


async def fetch_into(client, entity, count=150, do_news=True):
    """Забирает посты канала в content.db и news.db, качает фото. Возвращает сводку."""
    name = getattr(entity, "username", None) or "ub_%s" % entity.id
    print("канал: «%s» | id %s%s" % (getattr(entity, "title", "?"), entity.id,
                                     (" | @" + name) if getattr(entity, "username", None) else " (приватный)"))
    os.makedirs(MEDIA, exist_ok=True)
    cconn = content_db()
    nconn = news_db() if do_news else None
    added = skipped = photos = videos = updated = 0
    async for msg in client.iter_messages(entity, limit=count):
        raw_text = msg.message or ""
        if tl_html is not None:
            try:
                text = tl_html.unparse(raw_text, msg.entities or []).strip()
            except Exception:
                text = raw_text.strip()
        else:
            text = raw_text.strip()
        text = strip_html(scrub_selfref(text))
        album_id = str(getattr(msg, "media_group_id", None) or "")
        has_photo = bool(msg.photo)
        is_video = bool(getattr(msg, "video", None)) or bool(
            getattr(msg, "document", None) and (msg.document.mime_type or "").startswith("video"))
        # Пост может быть чистой картинкой без подписи — тогда text пустой, и старый
        # guard выбрасывал его ДО загрузки медиа. Такие посты теряли изображение.
        # То же касается продолжений альбома: подпись несёт только первый элемент.
        # Пропускаем любое сообщение, у которого есть медиа, даже без текста.
        if (not text or len(text) < 40) and not (has_photo or is_video):
            continue
        post_id = "%s/%s" % (entity.id, msg.id)
        # 图片 без текста: сохраняем ради медиа, но в публикацию как текстовый пост не пускаем
        is_media = 1 if ((has_photo or is_video) and len(text) < 40) else 0
        existing = cconn.execute(
            "SELECT id, text, image_local FROM items WHERE source = ? AND post_id = ?",
            (name, post_id)).fetchone()
        if existing:
            if "<a " in text and "<a " not in (existing[1] or ""):
                cconn.execute("UPDATE items SET text = ? WHERE id = ?", (text[:4000], existing[0]))
                updated += 1
            # Пост уже есть, а картинка не загрузилась в прошлый заход — докачиваем.
            if not existing[2] and has_photo:
                rel = await grab_media(client, msg, name, post_id, False)
                if rel:
                    cconn.execute("UPDATE items SET image_local = ? WHERE id = ?", (rel, existing[0]))
                    photos += 1
            skipped += 1
            continue
        # У продолжений альбома текста нет: у всех был бы одинаковый md5("") и они
        # отбрасывались бы как дубли друг друга. Ключ делаем уникальным на сообщение.
        h = hashlib.md5(text[:400].encode("utf-8")).hexdigest() if text else "mg%d" % msg.id
        dup = cconn.execute("SELECT id, image_local FROM items WHERE hash = ?", (h,)).fetchone()
        if dup:
            # Копия: запись не создаём, но если у оригинала нет картинки — навешиваем
            # картинку из копии. Раньше continue стоял до загрузки медиа, и копии
            # оставались без изображений.
            if not dup[1] and has_photo:
                rel = await grab_media(client, msg, name, post_id, False)
                if rel:
                    cconn.execute("UPDATE items SET image_local = ? WHERE id = ?", (rel, dup[0]))
                    photos += 1
            skipped += 1
            continue
        ts = msg.date.astimezone(MSK).isoformat()
        image_local = ""
        if has_photo:
            rel = await grab_media(client, msg, name, post_id, False)
            if rel:
                image_local = rel
                photos += 1
        video_local = ""
        if is_video:
            size = (getattr(msg, "file", None) and msg.file.size) or 0
            if 0 < size <= 47 * 1024 * 1024:
                rel = await grab_media(client, msg, name, post_id, True)
                if rel:
                    video_local = rel
                    videos += 1
        cconn.execute(
            "INSERT OR IGNORE INTO items (source, post_id, text, ts, views, image_url, image_local, video_local, "
            "hash, simhash, dupe, media_group_id, media_seq, is_media) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (name, post_id, text[:4000], ts, msg.views or 0, "", image_local, video_local, h,
             to_signed(simhash(text)), album_id, msg.id % 1000, is_media))
        # Продолжения альбома в news не идут: текста нет, публиковать нечего.
        if nconn is not None and text:
            nconn.execute(
                "INSERT OR IGNORE INTO news (source, lang, title, text, url, ts, image_url, hash, simhash) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (name, "ru", scrub_selfref(text.split("\n", 1)[0][:110], is_title=True), text[:1200],
                 ("https://t.me/%s/%s" % (name, msg.id)) if getattr(entity, "username", None) else "",
                 ts, "", "ub_" + h, to_signed(simhash(text))))
        added += 1
    cconn.commit()
    if nconn is not None:
        nconn.commit()
    print("импортировано: %d | уже было: %d | с фото: %d | с видео: %d | со ссылками обновлено: %d" % (added, skipped, photos, videos, updated))
    if do_news:
        print("(новостная база тоже пополнена — новостной конвейер сам отберёт, что достойно канала)")
    return {"imported": added, "skipped": skipped, "photos": photos, "videos": videos, "name": name}


async def main():
    args = sys.argv[1:]
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=PROXY)
    await client.connect()
    if not await client.is_user_authorized():
        print("нет авторизации — сначала login1.py + login2.py")
        return 1

    if "--list" in args or not args:
        n = 0
        async for d in client.iter_dialogs():
            ent = d.entity
            if getattr(ent, "broadcast", False) or getattr(ent, "megagroup", False):
                print("%-42s | id %-14s | @%s" % ((d.title or "")[:40], ent.id,
                                                  getattr(ent, "username", "") or "-"))
                n += 1
        print("всего чатов-каналов: %d" % n)
        return 0

    ref = args[0]
    count = int(args[args.index("--count") + 1]) if "--count" in args else 200
    do_news = "--no-news" not in args

    entity = await resolve(client, ref)
    if not entity:
        print("не нашёл канал: %s (можно без ссылки — часть названия)" % ref)
        return 1
    await fetch_into(client, entity, count, do_news)
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
