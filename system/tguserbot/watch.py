#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch.py — добирает новые посты из каналов watch.json (для launchd, каждые 30 мин)."""
import asyncio
import json
import os
import sys

import socks
from telethon import TelegramClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_channel as fc  # noqa: E402


async def main():
    wf = os.path.join(HERE, "watch.json")
    try:
        watch = json.load(open(wf, encoding="utf-8"))
    except FileNotFoundError:
        print("watch.json нет — нечего смотреть")
        return 0
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=fc.PROXY)
    await client.connect()
    if not await client.is_user_authorized():
        print("нет авторизации")
        return 1
    for w in watch:
        try:
            entity = await fc.resolve(client, w.get("ref") or w.get("key"))
            if not entity:
                print("не найден:", w.get("title"))
                continue
            print("── %s" % w.get("title"))
            await fc.fetch_into(client, entity, 40)
        except Exception as exc:
            print("ошибка (%s): %s" % (w.get("title"), type(exc).__name__))
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
