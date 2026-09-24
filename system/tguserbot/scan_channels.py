#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_channels.py — проходит по каналам аккаунта и определяет, чем они полезны нашим каналам.

Запуск: venv/bin/python scan_channels.py
Результат: таблица + ~/tguserbot/scan_result.json
"""
import asyncio
import json
import os
import re
import time
import urllib.request

import socks
from telethon import TelegramClient

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY = (socks.SOCKS5, "127.0.0.1", 1080)
BRIDGE = "http://127.0.0.1:3001/v1/chat/completions"
BRIDGE_TOKEN = "glm-local"
OURS = {"Нейро-секреты", "Нейро-секреты Chat", "Волшебные Промпты", "Волшебные Промпты Chat",
        "Нейро-Пульс", "Новости ИИ Chat", "Нейро Work", "Нейро Work Chat"}


def bridge_classify(batch):
    parts = []
    for i, (title, sample) in enumerate(batch, 1):
        parts.append("%d) «%s»:\n%s" % (i, title, sample[:1400]))
    system = ("Ты — редактор сети Telegram-каналов про ИИ: «Нейро-секретики» (секреты, лайфхаки и инструменты "
              "для жизни — дом, красота, отношения, дети), «Волшебные промпты» (готовые промпты и разборы), "
              "«Нейро-работа» (AI для работы: письма, документы, таблицы, автоматизация), "
              "«Нейро-Пульс» (новости ИИ). Для каждого канала из списка определи, чем он полезен нам. "
              'Ответ — строго JSON-массив без пояснений: '
              '[{"n": 1, "fits": "secrets|prompts|work|news|none", "note": "почему, до 10 слов"}]')
    payload = {"model": "glm-5.3",
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": "\n\n".join(parts)}],
               "temperature": 0.3, "max_tokens": 1000, "stream": False}
    txt = ""
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(BRIDGE, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Authorization": "Bearer " + BRIDGE_TOKEN,
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except Exception:
            txt = ""
        if txt.strip():
            break
        time.sleep(3)
    if not txt.strip():
        with open(os.path.join(HERE, "scan_bad.log"), "a", encoding="utf-8") as fh:
            fh.write("EMPTY REPLY for: %s\n" % ", ".join(t for t, _ in batch))
        return {}
    clean = re.sub(r"```(?:json)?", "", txt)
    m = re.search(r"\[.*\]", clean, re.S)
    if not m:
        with open(os.path.join(HERE, "scan_bad.log"), "a", encoding="utf-8") as fh:
            fh.write("NO ARRAY for %s\n%s\n\n" % (", ".join(t for t, _ in batch), txt[:800]))
        return {}
    raw = m.group(0)
    arr = None
    for candidate in (raw, re.sub(r",\s*([\]}])", r"\1", raw)):
        try:
            arr = json.loads(candidate)
            break
        except ValueError:
            continue
    if not isinstance(arr, list):
        with open(os.path.join(HERE, "scan_bad.log"), "a", encoding="utf-8") as fh:
            fh.write("BAD JSON for %s\n%s\n\n" % (", ".join(t for t, _ in batch), raw[:800]))
        return {}
    out = {}
    for idx, row in enumerate(arr):
        if not isinstance(row, dict):
            continue
        n = row.get("n")
        if isinstance(n, str) and n.strip().isdigit():
            n = int(n.strip())
        if not isinstance(n, int):
            n = idx + 1  # массив обычно по порядку
        if 1 <= n <= len(batch):
            out[batch[n - 1][0]] = row
    return out


async def main():
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=PROXY)
    await client.connect()
    chans = []
    async for d in client.iter_dialogs():
        ent = d.entity
        if not (getattr(ent, "broadcast", False) or getattr(ent, "megagroup", False)):
            continue
        title = (d.title or "").strip()
        if not title or title in OURS:
            continue
        texts = []
        try:
            async for msg in client.iter_messages(ent, limit=8):
                t = (msg.message or "").strip()
                if len(t) >= 80:
                    texts.append(t[:600])
                if len(texts) >= 4:
                    break
        except Exception:
            continue
        if not texts:
            continue
        chans.append((title, ent.id, "\n---\n".join(texts)))
    print("каналов к анализу: %d" % len(chans), flush=True)
    results = []
    B = 5
    total_b = (len(chans) + B - 1) // B
    for i in range(0, len(chans), B):
        batch = [(t, s) for t, _, s in chans[i:i + B]]
        try:
            res = bridge_classify(batch)
        except Exception as exc:
            print("батч %d ошибка: %s" % (i // B + 1, type(exc).__name__), flush=True)
            res = {}
        for title, cid, _ in chans[i:i + B]:
            row = res.get(title) or {}
            results.append({"title": title, "id": cid,
                            "fits": row.get("fits", "?"), "note": row.get("note", "")})
        print("батч %d/%d разобран" % (i // B + 1, total_b), flush=True)
        time.sleep(2)
    order = {"secrets": 0, "prompts": 1, "work": 2, "news": 3, "none": 9, "?": 8}
    results.sort(key=lambda r: (order.get(str(r["fits"]), 8), r["title"].lower()))
    print()
    print("=== РЕЗУЛЬТАТ ===")
    for r in results:
        if r["fits"] != "none":
            print("%-9s | %-42s | id %-14s | %s" % (r["fits"], r["title"][:40], r["id"], r["note"][:60]))
    json.dump(results, open(os.path.join(HERE, "scan_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\nполный список: ~/tguserbot/scan_result.json")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
