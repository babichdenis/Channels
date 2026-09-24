#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""competitors.py — мониторинг конкурентов: что у них заходит.

  --collect   собрать свежие посты конкурентов (t.me/s) вместе с просмотрами
  --report    анализ: топ-посты + что перенять (LLM) → правила в память + отчёт в бота
  --stats     сводка по конкурентам

Перенимаем МЕХАНИКИ и темы, а не копируем посты (правило CROSS_CHANNEL).
"""

import argparse
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conveyor import fetch, parse_tme, simhash, to_signed, bridge_chat, parse_json_reply, load_json, save_json, HERE

COMP = os.path.join(HERE, "competitors.json")
CONFIG = os.path.join(HERE, "sources.json")
MEMORY = os.path.join(HERE, "content_memory.json")

# Конкуренты с большой аудиторией (из валидации Telemetr)
COMPETITORS = [
    {"name": "misssvikkitut", "niche": "AI для жизни", "subs": 73000},
    {"name": "prompter_ru", "niche": "промпты", "subs": 188000},
    {"name": "unisetai", "niche": "промпты", "subs": 110000},
    {"name": "promtyi", "niche": "промпты", "subs": 92000},
    {"name": "aigenschool", "niche": "промпты", "subs": 66000},
    {"name": "ai2smm", "niche": "промпты", "subs": 59000},
    {"name": "dailyprompts", "niche": "промпты", "subs": 43000},
    {"name": "belyakai", "niche": "нейросети", "subs": 31000},
    {"name": "n2d2ai", "niche": "контент", "subs": 25000},
    {"name": "promptsul", "niche": "промпты", "subs": 15500},
]

ANALYST_SYSTEM = """Ты — аналитик контента. Даны посты конкурентов с просмотрами (топ по каждому каналу).
Определи: какие темы, форматы, механики и hooks у них заходят ЛУЧШЕ всего.
Верни строго JSON: {"top_patterns": ["..."], "best_hooks": ["..."], "topics_to_adopt": ["..."],
 "formats_to_adopt": ["..."], "avoid": ["..."], "confidence": "low|medium|high"}
Перенимаем МЕХАНИКИ, а не копируем тексты."""


def cmd_collect(cfg):
    data = load_json(COMP, {})
    posts = data.get("posts") or []
    seen = {p.get("hash") for p in posts}
    new = 0
    for comp in COMPETITORS:
        name = comp["name"]
        try:
            page = fetch("https://t.me/s/%s" % urllib.parse.quote(name), cfg.get("proxy"), timeout=25)
            items = parse_tme(page, name)
        except Exception as exc:
            print("✗ %-16s %s" % (name, str(exc)[:50]))
            continue
        fresh = 0
        for it in items:
            h = str(abs(simhash(it["text"])))
            if h in seen:
                continue
            seen.add(h)
            posts.append({"hash": h, "source": name, "niche": comp["niche"], "subs": comp["subs"],
                          "text": it["text"][:800], "views": it.get("views") or 0, "ts": it.get("ts")})
            fresh += 1
        new += fresh
        print("✓ %-16s постов %-3d новых %d" % (name, len(items), fresh))
        time.sleep(1)
    data["posts"] = posts[-2000:]
    save_json(COMP, data)
    print("новых постов конкурентов: %d | всего: %d" % (new, len(data["posts"])))
    return 0


def cmd_report(cfg):
    data = load_json(COMP, {})
    posts = data.get("posts") or []
    if not posts:
        print("нет данных — сначала --collect")
        return 1
    # топ-5 по просмотрам на канал
    by_source = {}
    for p in posts:
        by_source.setdefault(p["source"], []).append(p)
    top = []
    for source, items in by_source.items():
        for p in sorted(items, key=lambda x: -(x.get("views") or 0))[:5]:
            top.append("%s (%s, %s просм.): %s" % (source, p.get("niche"), p.get("views"),
                                                   p["text"][:160].replace("\n", " ")))
    try:
        reply = bridge_chat(cfg, ANALYST_SYSTEM, "Посты конкурентов:\n%s" % "\n".join(top[:60]))
    except Exception as exc:
        print("аналитик недоступен: %s" % str(exc)[:80])
        return 1
    result = parse_json_reply(reply) or {}
    # правила в память (перенимаем)
    memory = load_json(MEMORY, {})
    rules = memory.get("rules") or []
    for item in (result.get("topics_to_adopt") or [])[:5]:
        rules.append("перенять тему: %s" % item)
    for item in (result.get("formats_to_adopt") or [])[:5]:
        rules.append("перенять формат: %s" % item)
    for item in (result.get("best_hooks") or [])[:3]:
        rules.append("перенять hook: %s" % item)
    memory["rules"] = rules
    memory["competitors_updated_at"] = time.strftime("%Y-%m-%d %H:%M")
    save_json(MEMORY, memory)
    print("top_patterns:", len(result.get("top_patterns") or []))
    print("правил добавлено:", len(rules) - (len(rules) - (len(result.get("topics_to_adopt") or []) + len(result.get("formats_to_adopt") or []) + len(result.get("best_hooks") or []))))
    for key in ("top_patterns", "topics_to_adopt", "formats_to_adopt", "avoid"):
        for item in (result.get(key) or [])[:5]:
            print("  • %s" % item)
    # отчёт в бота
    try:
        import analytics
        analytics.notify_admin("🕵️ <b>Конкуренты: что заходит</b>\n\n" + "\n".join(
            "• %s" % x for x in (result.get("top_patterns") or [])[:5] + (result.get("topics_to_adopt") or [])[:5]))
    except Exception:
        pass
    return 0


def cmd_stats(cfg):
    data = load_json(COMP, {})
    posts = data.get("posts") or []
    by_source = {}
    for p in posts:
        by_source.setdefault(p["source"], []).append(p)
    print("%-16s %6s %8s %8s" % ("канал", "постов", "макс.просм", "сред.просм"))
    for source, items in sorted(by_source.items()):
        views = [p.get("views") or 0 for p in items]
        print("%-16s %6d %8d %8d" % (source, len(items), max(views), sum(views) // max(len(views), 1)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    cfg = load_json(CONFIG, {})
    if args.collect:
        return cmd_collect(cfg)
    if args.report:
        return cmd_report(cfg)
    if args.stats:
        return cmd_stats(cfg)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
