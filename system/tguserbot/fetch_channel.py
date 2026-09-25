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
        text = scrub_selfref(text)
        if not text or len(text) < 40:
            continue
        post_id = "%s/%s" % (entity.id, msg.id)
        existing = cconn.execute("SELECT id, text FROM items WHERE source = ? AND post_id = ?",
                                 (name, post_id)).fetchone()
        if existing:
            if "<a " in text and "<a " not in (existing[1] or ""):
                cconn.execute("UPDATE items SET text = ? WHERE id = ?", (scrub_selfref(text)[:4000], existing[0]))
                updated += 1
            skipped += 1
            continue
        h = hashlib.md5(text[:400].encode("utf-8")).hexdigest()
        if cconn.execute("SELECT 1 FROM items WHERE hash = ?", (h,)).fetchone():
            skipped += 1
            continue
        ts = msg.date.astimezone(MSK).isoformat()
        image_local = ""
        if msg.photo:
            fname = re.sub(r"[^\w.-]", "_", "%s-%s" % (name, post_id))[:90] + ".jpg"
            dest = os.path.join(MEDIA, fname)
            try:
                await client.download_media(msg, file=dest)
                if os.path.exists(dest) and os.path.getsize(dest) > 3000:
                    image_local = "media/" + fname
                    photos += 1
            except Exception:
                pass
        video_local = ""
        is_video = bool(getattr(msg, "video", None)) or bool(
            getattr(msg, "document", None) and (msg.document.mime_type or "").startswith("video"))
        if is_video:
            size = (getattr(msg, "file", None) and msg.file.size) or 0
            if 0 < size <= 47 * 1024 * 1024:
                fname = re.sub(r"[^\w.-]", "_", "%s-%s" % (name, post_id))[:90] + ".mp4"
                dest = os.path.join(MEDIA, fname)
                try:
                    await client.download_media(msg, file=dest)
                    if os.path.exists(dest) and os.path.getsize(dest) > 10000:
                        video_local = "media/" + fname
                        videos += 1
                except Exception:
                    pass
        cconn.execute(
            "INSERT OR IGNORE INTO items (source, post_id, text, ts, views, image_url, image_local, video_local, hash, simhash, dupe) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0)",
            (name, post_id, text[:4000], ts, msg.views or 0, "", image_local, video_local, h, to_signed(simhash(text))))
        if nconn is not None:
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
