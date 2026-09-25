#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vk_ingest.py — собирает новые посты VK-зеркал наших каналов в базу контента (content.db).

Список групп: ~/channels/vk_sources.json  {"groups": [{"screen": "neurohive", "title": "..."}]}
Токен: ~/secrets/vk_service_token
"""
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from conveyor import fetch, fetch_bytes, simhash, to_signed  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
VKSRC = os.path.join(HERE, "vk_sources.json")
TOKEN_FILE = os.path.expanduser("~/secrets/vk_user_token")
TOKEN_FILE_FALLBACK = os.path.expanduser("~/secrets/vk_service_token")


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def main():
    token = ""
    for tf in (TOKEN_FILE, TOKEN_FILE_FALLBACK):
        try:
            token = open(tf, encoding="utf-8").read().strip()
            if token:
                break
        except OSError:
            pass
    if not token:
        print("нет VK-токена")
        return 1
    groups = (load(VKSRC, {}) or {}).get("groups", [])
    if not groups:
        print("vk_sources.json пуст — нечего собирать")
        return 0
    proxy = (load(os.path.join(HERE, "sources.json"), {}) or {}).get("proxy")
    media = os.path.join(HERE, "media")
    os.makedirs(media, exist_ok=True)
    conn = sqlite3.connect(os.path.join(HERE, "content.db"), timeout=30)
    try:
        conn.execute("ALTER TABLE items ADD COLUMN video_local TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    total_new = 0
    for g in groups:
        screen = (g.get("screen") or "").strip()
        if not screen:
            continue
        try:
            page = fetch("https://api.vk.com/method/wall.get?domain=%s&count=15&access_token=%s&v=5.199"
                         % (screen, token), None, timeout=25)
            data = json.loads(page)
        except Exception as exc:
            print("✗ %s: %s" % (screen, type(exc).__name__))
            continue
        if data.get("error"):
            print("✗ %s: VK error %s" % (screen, data["error"].get("error_msg")))
            continue
        items = (data.get("response") or {}).get("items") or []
        new = 0
        for post in items:
            if post.get("marked_as_ads"):
                continue
            text = (post.get("text") or "").strip()
            if len(text) < 40:
                continue
            source = "vk_" + screen
            post_id = "vk_%s_%s" % (post.get("owner_id"), post.get("id"))
            if conn.execute("SELECT 1 FROM items WHERE source = ? AND post_id = ?",
                            (source, post_id)).fetchone():
                continue
            h = hashlib.md5(text[:400].encode("utf-8")).hexdigest()
            if conn.execute("SELECT 1 FROM items WHERE hash = ?", (h,)).fetchone():
                continue
            image_local = ""
            for att in (post.get("attachments") or []):
                if att.get("type") != "photo":
                    continue
                sizes = ((att.get("photo") or {}).get("sizes") or [])
                if not sizes:
                    continue
                best = max(sizes, key=lambda s: s.get("width") or 0)
                url = best.get("url") or ""
                if not url:
                    continue
                fname = "vk-%s-%s.jpg" % (screen, post.get("id"))
                try:
                    blob = fetch_bytes(url, proxy, timeout=60)
                    if len(blob) > 3000:
                        with open(os.path.join(media, fname), "wb") as fh:
                            fh.write(blob)
                        image_local = "media/" + fname
                except Exception:
                    pass
                break
            ts = datetime.fromtimestamp(post.get("date") or time.time(), MSK).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO items (source, post_id, text, ts, views, image_url, image_local, hash, simhash, dupe) "
                "VALUES (?,?,?,?,?,?,?,?,?,0)",
                (source, post_id, text[:4000], ts, post.get("views", {}).get("count", 0) if isinstance(post.get("views"), dict) else 0,
                 "", image_local, h, to_signed(simhash(text))))
            new += 1
        conn.commit()
        total_new += new
        print("✓ %-20s постов %-3d новых %d" % (screen, len(items), new))
        time.sleep(1)
    print("итого новых:", total_new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
