#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Одноразовый дебаг классификатора скана."""
import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.expanduser("~/tguserbot"))
import scan_channels as sc  # noqa: E402
from telethon import TelegramClient  # noqa: E402

HERE = os.path.expanduser("~/tguserbot")


async def main():
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    client = TelegramClient(os.path.join(HERE, "user"), cfg["api_id"], cfg["api_hash"], proxy=sc.PROXY)
    await client.connect()
    titles = ["Вайб-кодинг", "Neurosphere News", "Нейросети | AI-агентура | Claude Code"]
    samples = []
    async for d in client.iter_dialogs():
        t = (d.title or "")
        for want in titles:
            if want.lower() in t.lower():
                texts = []
                async for msg in client.iter_messages(d.entity, limit=6):
                    x = (msg.message or "").strip()
                    if len(x) >= 80:
                        texts.append(x[:600])
                    if len(texts) >= 3:
                        break
                if texts:
                    samples.append((t, "\n---\n".join(texts)))
                break
    await client.disconnect()
    print("каналов в тесте:", len(samples))
    parts = []
    for i, (title, sample) in enumerate(samples, 1):
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
    req = urllib.request.Request(sc.BRIDGE, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + sc.BRIDGE_TOKEN,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    raw = ((data.get("choices") or [{}])[0].get("message", {}).get("content", ""))
    print("RAW REPLY (700):")
    print(raw[:700])


if __name__ == "__main__":
    asyncio.run(main())
