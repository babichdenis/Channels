#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""channel_finder.py — ищет новые Telegram-каналы по ИИ-тематике через userbot и добавляет
подходящие публичные каналы в источники (контент + новости). Отчёт приходит в бота.

Использование: venv/bin/python channel_finder.py [--dry]
"""
import asyncio
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.expanduser("~/tguserbot"))
sys.path.insert(0, os.path.expanduser("~/channels"))
import fetch_channel as fc  # noqa: E402
from conveyor import fetch as ch_fetch, parse_tme as ch_parse  # noqa: E402
from telethon import TelegramClient  # noqa: E402
from telethon.errors import FloodWaitError, UserAlreadyParticipantError  # noqa: E402
from telethon.tl.functions.channels import JoinChannelRequest  # noqa: E402
from telethon.tl.functions.contacts import SearchRequest  # noqa: E402

CH = os.path.expanduser("~/channels")
SRC = os.path.join(CH, "sources.json")
NEWS = os.path.join(CH, "news_sources.json")
BRIDGE = "http://127.0.0.1:3001/v1/chat/completions"
BRIDGE_TOKEN = "glm-local"
QUERIES = ["нейросети", "нейросети для жизни", "промпты нейросети", "AI инструменты",
           "искусственный интеллект новости", "ChatGPT лайфхаки", "нейросети для работы"]
MAX_ADD = 5
MAX_JOIN = 3  # не больше подписок за прогон (бережём аккаунт)


def bridge(system, user, timeout=180):
    payload = {"model": "glm-5.3",
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "temperature": 0.3, "max_tokens": 1000, "stream": False}
    req = urllib.request.Request(BRIDGE, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + BRIDGE_TOKEN,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def classify_batch(batch):
    parts = []
    for i, (title, sample) in enumerate(batch, 1):
        parts.append("%d) «%s»:\n%s" % (i, title, sample[:1200]))
    system = ("Ты — редактор сети Telegram-каналов про ИИ: «Нейро-секретики» (секреты, лайфхаки, инструменты "
              "для жизни), «ПромптКлад» (готовые промпты), «Нейропомощник» (AI для работы), "
              "«НейроСигнал» (новости ИИ). Для каждого канала скажи, чем он полезен, или none. "
              'Ответ — строго JSON-массив: [{"n": 1, "fits": "secrets|prompts|work|news|none", "note": "до 8 слов"}]')
    try:
        txt = bridge(system, "\n\n".join(parts))
    except Exception:
        return {}
    m = re.search(r"\[.*\]", re.sub(r"```(?:json)?", "", txt), re.S)
    if not m:
        return {}
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        return {}
    out = {}
    for idx, row in enumerate(arr):
        if not isinstance(row, dict):
            continue
        n = row.get("n")
        if isinstance(n, str) and n.strip().isdigit():
            n = int(n.strip())
        if not isinstance(n, int):
            n = idx + 1
        if 1 <= n <= len(batch):
            out[batch[n - 1][0]] = row
    return out


def send_report(added, joined=None):
    try:
        env = {}
        for line in open(os.path.join(CH, ".env"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        cfg = json.load(open(os.path.join(CH, "config.json"), encoding="utf-8"))
        admin = str(cfg.get("admin_chat_id") or "")
        token = env.get("TG_TOKEN_CHANNELS", "")
        if not (admin and token):
            return
        lines = ["🔍 Нашёл и добавил в источники %d канал(ов):" % len(added), ""]
        for a in added:
            lines.append("• @%s — %s (%s)" % (a["uname"], a["fits"], a["note"][:60]))
        lines.append("")
        lines.append("Копии их постов пойдут в наши каналы (в бот на утверждение). "
                      "Отключить — скажи «убери @имя».")
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token,
                                     data=json.dumps({"chat_id": admin,
                                                      "text": "\n".join(lines)[:3500]}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)
    except Exception as exc:
        print("отчёт не ушёл: %s" % type(exc).__name__)


async def main():
    dry = "--dry" in sys.argv
    cfg = json.load(open(os.path.join(os.path.expanduser("~/tguserbot"), "config.json"), encoding="utf-8"))
    client = TelegramClient(fc.os.path.expanduser("~/tguserbot/user"), cfg["api_id"], cfg["api_hash"],
                            proxy=fc.PROXY)
    await client.connect()

    src = json.load(open(SRC, encoding="utf-8"))
    news = json.load(open(NEWS, encoding="utf-8"))
    known = {s.get("name", "").lower() for s in src.get("sources", [])}
    known |= {s.get("name", "").lower() for s in news.get("tg", [])}

    found = {}
    for q in QUERIES:
        try:
            res = await client(SearchRequest(q=q, limit=12))
            for chat in res.chats:
                uname = getattr(chat, "username", None)
                if uname and getattr(chat, "broadcast", False):
                    found[uname] = getattr(chat, "title", uname)
        except Exception as exc:
            print("поиск «%s»: %s" % (q, type(exc).__name__))
        await asyncio.sleep(2)

    new_names = {u: t for u, t in found.items() if u.lower() not in known and u.lower() != "telegram"}
    print("найдено новых каналов:", len(new_names))

    proxy = src.get("proxy")
    candidates = []
    for uname, title in list(new_names.items())[:30]:
        try:
            page = ch_fetch("https://t.me/s/%s" % uname, proxy, timeout=20)
        except Exception as exc:
            print("  превью @%s: %s" % (uname, type(exc).__name__))
            continue
        posts = ch_parse(page, uname)
        if len(posts) < 3:
            continue  # нет открытого превью — не берём
        sample = "\n---\n".join((p["text"] or "")[:400] for p in posts[:3])
        candidates.append((uname, title, sample))
    print("с открытым превью:", len(candidates))

    added = []
    for i in range(0, len(candidates), 5):
        batch = [(t, s) for _, t, s in candidates[i:i + 5]]
        res = classify_batch(batch)
        for uname, title, _ in candidates[i:i + 5]:
            row = res.get(title) or {}
            fits = str(row.get("fits") or "none").lower()
            if fits in ("secrets", "prompts", "work", "news") and len(added) < MAX_ADD:
                added.append({"uname": uname, "title": title, "fits": fits,
                              "note": str(row.get("note") or "")})

    joined = []
    if added and not dry:
        for a in added:
            if not any(s.get("name", "").lower() == a["uname"].lower() for s in src.get("sources", [])):
                src.setdefault("sources", []).append({"name": a["uname"], "lang": "ru",
                                                      "topic": a["fits"], "weight": 2})
            if a["fits"] == "news" and not any(s.get("name", "").lower() == a["uname"].lower()
                                               for s in news.get("tg", [])):
                news.setdefault("tg", []).append({"name": a["uname"], "lang": "ru", "weight": 2})
        json.dump(src, open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        json.dump(news, open(NEWS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

        # подписываемся на лучшие и ставим на авто-наблюдение (история + репосты)
        watch_path = os.path.expanduser("~/tguserbot/watch.json")
        try:
            watch = json.load(open(watch_path, encoding="utf-8"))
        except Exception:
            watch = []
        for a in added[:MAX_JOIN]:
            try:
                await client(JoinChannelRequest(a["uname"]))
            except UserAlreadyParticipantError:
                pass
            except FloodWaitError as exc:
                print("flood wait %ss — прекращаю подписки" % exc.seconds)
                break
            except Exception as exc:
                print("join @%s: %s" % (a["uname"], type(exc).__name__))
                continue
            if not any(w.get("key") == a["uname"] for w in watch):
                watch.append({"key": a["uname"], "ref": "@" + a["uname"], "title": a["title"]})
            joined.append(a)
            await asyncio.sleep(2)
        json.dump(watch, open(watch_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        # сразу подтягиваем историю подписанных
        for a in joined:
            try:
                ent = await fc.resolve(client, "@" + a["uname"])
                if ent:
                    print("── история @%s" % a["uname"])
                    await fc.fetch_into(client, ent, 60)
            except Exception as exc:
                print("история @%s: %s" % (a["uname"], type(exc).__name__))
        send_report(added, joined)

    print("добавлено:", len(added), "| подписался:", len(joined))
    for a in added:
        mark = "+" if a in joined else " "
        print("  %s @%s | %s | %s" % (mark, a["uname"], a["fits"], a["note"][:50]))
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
