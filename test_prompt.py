#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prompt.py — проверка единого промпта канала на другой нейросети.

Берёт свежие посты из content.db, прогоняет через указанную модель и печатает черновики.

    python3 test_prompt.py --prompt PROMPT_CHANNEL_NEURO_SECRETS.md \
        --base http://127.0.0.1:3000/v1 --key deeprouter-local --model deepseek-v4.1-flash --n 2
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "content.db")


NEWS_MARKERS = ("выпустил", "представил", "анонсировал", "релиз", "обновил", "версия",
                "раунд", "инвестиц", "стоимость акций", "подорожал", "подешевел", "запустила новую")
PRACTICAL_MARKERS = ("промпт", "запрос", "попробуй", "совет", "лайфхак", "как ", "способ",
                     "секрет", "используй", "пример", "шаг", "сделай")


def source_posts(n):
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT source, text, views FROM items WHERE used = 0 AND LENGTH(text) > 250 "
        "ORDER BY views DESC LIMIT 250").fetchall()
    conn.close()
    scored = []
    for source, text, views in rows:
        low = (text or "").lower()
        score = views or 0
        if any(m in low for m in PRACTICAL_MARKERS):
            score += 20000
        if any(m in low for m in NEWS_MARKERS):
            score -= 150000
        if score > 0:
            scored.append((score, source, text, views))
    scored.sort(reverse=True)
    # разнообразие: сначала лучший пост с каждого источника
    best_per_source, seen = [], set()
    for row in scored:
        if row[1] not in seen:
            seen.add(row[1])
            best_per_source.append(row)
    rest = [row for row in scored if row not in best_per_source]
    pool = best_per_source + rest
    return [(s, t, v) for _, s, t, v in pool[:max(n, n * 2)]]


def call(base, key, model, system, user, timeout=240):
    payload = {"model": model, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.7, "max_tokens": 900, "stream": False}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def parse_json_reply(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--n", type=int, default=2)
    args = ap.parse_args()

    system = open(os.path.join(HERE, args.prompt), encoding="utf-8").read()
    print("=== %s | модель %s ===" % (args.prompt, args.model))
    ok = skip = err = 0
    for source, text, views in source_posts(args.n * 2):
        if ok >= args.n:
            break
        try:
            reply = call(args.base, args.key, args.model, system, "Источник: %s\n\n%s" % (source, text[:2500]))
        except Exception as exc:
            err += 1
            print("✗ %s: %s: %s" % (source, type(exc).__name__, str(exc)[:90]))
            continue
        draft = parse_json_reply(reply)
        if draft and draft.get("skip"):
            skip += 1
            print("· пропуск (%s)" % source)
            continue
        if not draft or not draft.get("text"):
            err += 1
            print("✗ %s: не JSON: %s" % (source, reply[:150].replace("\n", " ")))
            continue
        ok += 1
        print("\n----- %s (%s, %s просм.) -----" % (draft.get("rubric"), source, views))
        print(draft["text"])
        print("хэштеги:", " ".join(draft.get("hashtags") or []))
        print("картинка:", (draft.get("image_prompt_en") or draft.get("image_prompt") or "")[:110])
    print("\nитог: ок %d, пропуск %d, ошибок %d" % (ok, skip, err))
    return 0


if __name__ == "__main__":
    sys.exit(main())
