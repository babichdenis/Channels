#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""login3.py — шаг 2b: облачный пароль (2FA). Использование: login3.py <пароль>"""
import asyncio
import json
import os
import sys

import socks
from telethon import TelegramClient

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY = (socks.SOCKS5, "127.0.0.1", 1080)


async def main():
    if len(sys.argv) < 2:
        print("использование: login3.py <пароль 2FA>")
        return 1
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=PROXY)
    await client.connect()
    await client.sign_in(password=sys.argv[1].strip())
    me = await client.get_me()
    print("✅ Вошли (2FA):", me.first_name, "@%s" % me.username if me.username else me.phone)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
