#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch.py — добирает новые посты из каналов watch.json (для launchd, каждые 30 мин)."""
import asyncio
import errno
import fcntl
import json
import os
import sys

import socks
from telethon import TelegramClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_channel as fc  # noqa: E402

DEADLINE = 900  # весь проход не дольше 15 мин, иначе выходим и отпускаем блокировку сессии


def acquire_lock():
    """Одна копия за раз: вторая иначе падает с 'database is locked'."""
    fh = open(os.path.join(HERE, "watch.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError) as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            fh.close()
            return None
        raise
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


async def main():
    lock = acquire_lock()
    if lock is None:
        print("другой инстанс watch.py уже работает — пропускаю", flush=True)
        return 0
    wf = os.path.join(HERE, "watch.json")
    try:
        watch = json.load(open(wf, encoding="utf-8"))
    except FileNotFoundError:
        print("watch.json нет — нечего смотреть", flush=True)
        return 0
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=fc.PROXY)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("нет авторизации", flush=True)
            return 1
        for w in watch:
            try:
                entity = await fc.resolve(client, w.get("ref") or w.get("key"))
                if not entity:
                    print("не найден:", w.get("title"), flush=True)
                    continue
                print("── %s" % w.get("title"), flush=True)
                await fc.fetch_into(client, entity, 40)
            except Exception as exc:
                print("ошибка (%s): %s" % (w.get("title"), type(exc).__name__), flush=True)
        return 0
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(asyncio.wait_for(main(), timeout=DEADLINE)))
    except asyncio.TimeoutError:
        print("таймаут %dс — выхожу, чтобы не держать блокировку сессии" % DEADLINE, flush=True)
        raise SystemExit(2)
