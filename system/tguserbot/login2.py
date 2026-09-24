#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""login2.py — шаг 2: войти по коду. Использование: login2.py <код>"""
import asyncio
import json
import os
import sys

import socks
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY = (socks.SOCKS5, "127.0.0.1", 1080)


async def main():
    if len(sys.argv) < 2:
        print("использование: login2.py <код>")
        return 1
    code = sys.argv[1].strip()
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    ph = json.load(open(os.path.join(HERE, "code.json"), encoding="utf-8"))["phone_code_hash"]
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=PROXY)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print("Уже авторизован:", me.first_name, "@%s" % me.username if me.username else me.phone)
        return 0
    try:
        await client.sign_in(cfg["phone"], code=code, phone_code_hash=ph)
    except SessionPasswordNeededError:
        print("НУЖЕН_ПАРОЛЬ_2FA")
        return 2
    me = await client.get_me()
    print("✅ Вошли:", me.first_name, "@%s" % me.username if me.username else me.phone)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
