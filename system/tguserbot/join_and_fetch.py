#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""join_and_fetch.py — подписывается на канал (в т.ч. закрытый по инвайту) и забирает посты."""
import asyncio
import json
import os
import re
import sys

import socks
from telethon import TelegramClient
from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_channel as fc  # noqa: E402


async def main():
    if len(sys.argv) < 2:
        print("использование: join_and_fetch.py <t.me/+invite | t.me/name | @name> [--count 150] [--no-join]")
        return 1
    ref = sys.argv[1].strip()
    count = int(sys.argv[sys.argv.index("--count") + 1]) if "--count" in sys.argv else 150
    no_join = "--no-join" in sys.argv
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=fc.PROXY)
    await client.connect()
    if not await client.is_user_authorized():
        print("нет авторизации (login1/login2)")
        return 1

    status = ""
    m = re.search(r"t\.me/\+([A-Za-z0-9_-]+)", ref)
    if m and not no_join:
        try:
            await client(ImportChatInviteRequest(m.group(1)))
            status = "подписался по инвайту"
        except UserAlreadyParticipantError:
            status = "уже подписан"
        except Exception as exc:
            status = "инвайт не сработал (%s)" % type(exc).__name__
    elif not m and not no_join:
        name = ref.replace("https://t.me/", "").lstrip("@").strip("/")
        if re.match(r"^[A-Za-z0-9_]{4,32}$", name):
            try:
                await client(JoinChannelRequest(name))
                status = "подписался"
            except UserAlreadyParticipantError:
                status = "уже подписан"
            except Exception as exc:
                status = "join не сработал (%s)" % type(exc).__name__

    entity = await fc.resolve(client, ref)
    if not entity:
        print((status + " | " if status else "") + "канал не нашёл: %s" % ref)
        return 1
    if status:
        print(status)
    await fc.fetch_into(client, entity, count)

    wf = os.path.join(HERE, "watch.json")
    try:
        watch = json.load(open(wf, encoding="utf-8"))
    except FileNotFoundError:
        watch = []
    key = getattr(entity, "username", None) or str(entity.id)
    if not any(w.get("key") == key for w in watch):
        watch.append({"key": key, "ref": ref, "title": getattr(entity, "title", key)})
        json.dump(watch, open(wf, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("добавлен в авто-наблюдение (проверка каждые 30 минут)")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
