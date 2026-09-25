#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tg_search_test.py — что реально показывает поиск Telegram по нашим ключевым словам и брендам."""
import asyncio
import json
import os

import socks
from telethon import TelegramClient
from telethon.tl.functions.contacts import SearchRequest

HERE = os.path.expanduser("~/tguserbot")

QUERIES = [
    # частотные ключи — смотрим конкуренцию
    "нейросети", "нейросети для фото", "нейросети для работы",
    "промпты", "новости ии", "нейросети и ии",
    # бренды-кандидаты — смотрим, свободны ли
    "нейронаходки", "промптология", "нейрообраз", "нейродело",
    "нейропульс", "нейрорадар", "нейромагия", "нейросекреты",
]


async def main():
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"],
                            proxy=("socks5", "127.0.0.1", 1080))
    await client.connect()
    if not await client.is_user_authorized():
        print("нет сессии")
        return
    for q in QUERIES:
        try:
            res = await client(SearchRequest(q=q, limit=14))
            chans = [c for c in res.chats if getattr(c, "title", None)
                     and not getattr(c, "left", False) or True]
            chans = [c for c in res.chats if getattr(c, "title", None)]
            print("═══ «%s» — найдено %d ═══" % (q, len(chans)))
            for c in chans[:7]:
                u = getattr(c, "username", None) or "-"
                print("   %-42s @%s" % (c.title[:42], u))
        except Exception as ex:
            print("═══ «%s» ОШИБКА: %s" % (q, str(ex)[:90]))
        await asyncio.sleep(2.5)
    await client.disconnect()


asyncio.run(main())
