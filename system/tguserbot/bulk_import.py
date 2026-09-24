#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bulk_import.py — разовый массовый импорт выбранных каналов + добавление в авто-наблюдение."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/tguserbot"))
import fetch_channel as fc  # noqa: E402
from telethon import TelegramClient  # noqa: E402

REFS = [
    "Интересные Нейросети",
    "Легкий путь в Python",
    "ChatGPT Jadve AI | News",
    "AI скептик",
    "Data Science | Machinelearning [ru]",
    "Лучшие боты Telegram",
    "byNara AI",
    "Neurosphere News",
    "Machinelearning",
    "Вайб-кодинг",
    "Нейросети | AI-агентура | Claude Code",
    "End Soft | Скрипты, Новости",
]


async def main():
    cfg = json.load(open(os.path.expanduser("~/tguserbot/config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.expanduser("~/tguserbot/user"), cfg["api_id"], cfg["api_hash"], proxy=fc.PROXY)
    await client.connect()
    watch_path = os.path.expanduser("~/tguserbot/watch.json")
    try:
        watch = json.load(open(watch_path, encoding="utf-8"))
    except FileNotFoundError:
        watch = []
    added_new = 0
    for ref in REFS:
        try:
            ent = await fc.resolve(client, ref)
            if not ent:
                print("НЕ НАЙДЕН: %s" % ref, flush=True)
                continue
            print("── %s" % ref, flush=True)
            await fc.fetch_into(client, ent, 80)
            key = getattr(ent, "username", None) or str(ent.id)
            if not any(w.get("key") == key for w in watch):
                watch.append({"key": key, "ref": ref, "title": getattr(ent, "title", key)})
                added_new += 1
        except Exception as exc:
            print("ошибка (%s): %s" % (ref, type(exc).__name__), flush=True)
    json.dump(watch, open(watch_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("в авто-наблюдении новых каналов: %d | всего: %d" % (added_new, len(watch)), flush=True)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
