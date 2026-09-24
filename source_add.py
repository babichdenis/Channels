#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_add.py — добавляет Telegram-канал в источники (контент + новости) и тянет свежие посты.

Использование: python3 source_add.py <username|ссылка>
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conveyor  # noqa: E402

SRC = os.path.join(HERE, "sources.json")
NEWS = os.path.join(HERE, "news_sources.json")


def load(path):
    return json.load(open(path, encoding="utf-8"))


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    if len(sys.argv) < 2:
        print("использование: source_add.py <username | t.me/...>")
        return 1
    raw = sys.argv[1].strip()
    m_inv = re.search(r"t\.me/\+[A-Za-z0-9_-]{8,}", raw)
    if m_inv:
        vpy = os.path.expanduser("~/tguserbot/venv/bin/python")
        script = os.path.expanduser("~/tguserbot/join_and_fetch.py")
        if not os.path.exists(vpy):
            print("userbot не настроен (~/tguserbot)")
            return 1
        try:
            out = subprocess.run([vpy, script, m_inv.group(0)], capture_output=True, text=True, timeout=900)
            print((out.stdout or out.stderr or "").strip()[:2500])
        except Exception as exc:
            print("userbot ошибка: %s" % str(exc)[:120])
        return 0
    name = re.sub(r"^https?://", "", raw)
    name = re.sub(r"^t\.me/(?:s/)?", "", name)
    name = name.lstrip("@").split("?")[0].split("/")[0].strip()
    if not re.match(r"^[A-Za-z0-9_]{4,32}$", name):
        print("не похоже на имя канала: %s" % raw[:80])
        return 1

    cfg = load(SRC)
    ncfg = load(NEWS)
    dry = "--dry" in sys.argv
    proxy = cfg.get("proxy")
    in_content = any(s.get("name", "").lower() == name.lower() for s in cfg.get("sources", []))
    in_news = any(s.get("name", "").lower() == name.lower() for s in ncfg.get("tg", []))

    try:
        page = conveyor.fetch("https://t.me/s/%s" % name, proxy, timeout=30)
    except Exception as exc:
        print("не удалось открыть t.me/%s (%s)" % (name, type(exc).__name__))
        return 1
    title_m = re.search(r'tgme_channel_info_header_title[^>]*>(.*?)</div>', page, re.S)
    title = name
    if title_m:
        title = re.sub(r"<[^>]+>", " ", title_m.group(1))
        title = re.sub(r"\s+", " ", title).strip() or name
    desc_m = re.search(r'tgme_channel_info_description[^>]*>(.*?)</div>', page, re.S)
    desc = ""
    if desc_m:
        desc = re.sub(r"<[^>]+>", " ", desc_m.group(1))
        desc = re.sub(r"\s+", " ", desc).strip()
    posts = conveyor.parse_tme(page, name)

    if in_content and in_news:
        print("ℹ️ Канал «%s» (@%s) уже подключён (контент + новости)." % (title, name))
        return 0

    lines = []
    if not in_content:
        if not dry:
            cfg.setdefault("sources", []).append({"name": name, "lang": "ru", "topic": "mixed", "weight": 2})
            save(SRC, cfg)
        lines.append("добавлен в контентные источники")
    if not in_news:
        if not dry:
            ncfg.setdefault("tg", []).append({"name": name, "lang": "ru", "weight": 2})
            save(NEWS, ncfg)
        lines.append("добавлен в новостной мониторинг")

    imported = ""
    if not in_content and not dry:
        mini = {"proxy": proxy, "sources": [{"name": name, "kind": "tg", "lang": "ru", "topic": "mixed"}]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            conveyor.cmd_ingest(mini)
        tail = [l for l in buf.getvalue().strip().splitlines() if l.strip()]
        imported = tail[-1] if tail else ""

    print("%s Канал «%s» (@%s): %s." % ("🔍 DRY" if dry else "✅", title, name,
                                         ", ".join(lines) if lines else "без изменений"))
    if desc:
        print("Описание: %s" % desc[:180])
    print("Постов в веб-превью: %d" % len(posts))
    if imported:
        print(imported)
    if not posts:
        print("⚠️ Постов в превью нет — автозабор может не работать (закрытое превью).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
