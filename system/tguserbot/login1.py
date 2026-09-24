#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""login1.py — шаг 1: запросить код подтверждения у Telegram."""
import asyncio
import json
import os

import socks
from telethon import TelegramClient

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY = (socks.SOCKS5, "127.0.0.1", 1080)


async def main():
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=PROXY)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print("Уже авторизован:", me.first_name, "@%s" % me.username if me.username else me.phone)
        return 0
    sent = await client.send_code_request(cfg["phone"])
    json.dump({"phone_code_hash": sent.phone_code_hash}, open(os.path.join(HERE, "code.json"), "w"))
    print("Код отправлен на %s" % cfg["phone"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
