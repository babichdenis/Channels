#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analytics.py — аналитика постов и Learning Loop (постоянно).

  --report    недельный отчёт: наблюдения → гипотезы → действия (LLM)
  --rules     правила для следующей недели → content_memory.json (используются планировщиком)
  --stats     быстрая сводка по очереди (без LLM)

Метрики: внутренние (публикации, одобрения, QC, рубрики, hooks, источники).
Внешние (просмотры/сохранения) — пока вручную: python3 analytics.py --external <id> <views> <reactions>
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conveyor import load_json, save_json, bridge_chat, parse_json_reply, HERE

CFG = load_json(os.path.join(HERE, "sources.json"), {})

QUEUE = os.path.join(HERE, "queue.json")
MEMORY = os.path.join(HERE, "content_memory.json")
REPORTS = os.path.join(HERE, "reports")
MSK = ZoneInfo("Europe/Moscow")

ANALYST_SYSTEM = """Ты — аналитик контентной сети. Дан отчёт за неделю по каналам.
Дай: 1) наблюдения (что видно по данным), 2) гипотезы (возможные причины), 3) действия (что менять).
Не считай корреляцию причиной: используй «наблюдается связь», «возможная причина», «гипотеза».
Учитывай размер выборки; если данных мало — confidence: low.
Верни строго JSON: {"observations": [], "hypotheses": [], "actions": [],
"keep": [], "reduce": [], "test": [], "stop": [], "new": [], "confidence": "low|medium|high"}"""


def week_posts():
    queue = load_json(QUEUE, [])
    since = (datetime.now(MSK) - timedelta(days=7)).isoformat()
    return [p for p in queue if (p.get("published_at") or p.get("scheduled_at") or "") >= since]


def build_stats(posts):
    stats = {
        "published": sum(1 for p in posts if p.get("status") == "published"),
        "pending": sum(1 for p in posts if p.get("status") == "pending"),
        "rejected": sum(1 for p in posts if p.get("status") == "rejected"),
        "library": sum(1 for p in posts if p.get("status") == "library"),
        "by_channel": dict(Counter(p.get("channel") for p in posts)),
        "by_rubric": dict(Counter(p.get("rubric") for p in posts)),
        "by_hook": dict(Counter((p.get("content_dna") or {}).get("hook_type") for p in posts if p.get("content_dna"))),
        "by_mechanic": dict(Counter((p.get("content_dna") or {}).get("mechanic") for p in posts if p.get("content_dna"))),
        "by_origin": dict(Counter(p.get("origin") for p in posts if p.get("origin"))),
    }
    qc = [p["quality"] for p in posts if p.get("quality")]
    if qc:
        for key in ("value", "saveability", "actionability", "naturalness"):
            vals = [q.get(key) for q in qc if isinstance(q.get(key), (int, float))]
            stats["avg_" + key] = round(sum(vals) / len(vals), 1) if vals else None
    return stats


def notify_admin(text):
    """Отправляет текст админу через бота (прокси для Telegram)."""
    try:
        env = {}
        for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        token = env.get("TG_TOKEN_CHANNELS", "")
        cfg = load_json(os.path.join(HERE, "config.json"), {})
        admin = str(cfg.get("admin_chat_id") or "")
        if not token or not admin:
            return False
        proxy = "http://127.0.0.1:3128"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        data = urllib.parse.urlencode({"chat_id": admin, "text": text[:4000],
                                       "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        opener.open(urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=data), timeout=60)
        return True
    except Exception:
        return False


def cmd_stats():
    stats = build_stats(week_posts())
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_report():
    posts = week_posts()
    stats = build_stats(posts)
    os.makedirs(REPORTS, exist_ok=True)
    if not posts:
        print("за неделю нет постов")
        return 0
    try:
        reply = bridge_chat(CFG, ANALYST_SYSTEM, "Отчёт за неделю:\n%s" % json.dumps(stats, ensure_ascii=False))
    except Exception as exc:
        print("аналитик недоступен: %s" % str(exc)[:80])
        return 1
    data = parse_json_reply(reply) or {}
    path = os.path.join(REPORTS, "report_%s.md" % datetime.now(MSK).strftime("%Y-%m-%d"))
    lines = ["# Отчёт за неделю (%s)" % datetime.now(MSK).strftime("%Y-%m-%d"), "",
             "## Данные", "```json", json.dumps(stats, ensure_ascii=False, indent=2), "```", ""]
    for key, title in (("observations", "Наблюдения"), ("hypotheses", "Гипотезы"), ("actions", "Действия")):
        if data.get(key):
            lines.append("## %s" % title)
            lines += ["- %s" % x for x in data[key]]
            lines.append("")
    lines.append("confidence: %s" % data.get("confidence", "?"))
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    save_json(os.path.join(REPORTS, "last_report.json"), data)
    print("отчёт:", path)
    print("наблюдений:", len(data.get("observations") or []), "| действий:", len(data.get("actions") or []))
    lines_tg = ["📊 <b>Отчёт за неделю</b>", ""]
    for key, title in (("observations", "👀 Наблюдения"), ("hypotheses", "💡 Гипотезы"), ("actions", "🎯 Действия")):
        for item in (data.get(key) or [])[:4]:
            lines_tg.append("%s: %s" % (title, item))
    lines_tg.append("")
    lines_tg.append("confidence: %s" % data.get("confidence", "?"))
    if notify_admin("\n".join(lines_tg)):
        print("отчёт отправлен в бота")
    return 0


def cmd_rules():
    data = load_json(os.path.join(REPORTS, "last_report.json"), {})
    if not data:
        print("сначала --report")
        return 1
    memory = load_json(MEMORY, {})
    rules = []
    for key in ("keep", "reduce", "test", "stop", "new"):
        for item in (data.get(key) or []):
            rules.append("%s: %s" % (key, item))
    memory["rules"] = rules
    memory["updated_at"] = datetime.now(MSK).isoformat(timespec="seconds")
    memory["confidence"] = data.get("confidence", "low")
    save_json(MEMORY, memory)
    print("правил записано:", len(rules))
    for r in rules[:8]:
        print("  •", r)
    if rules:
        notify_admin("🧠 <b>Правила на следующую неделю</b>\n\n" + "\n".join("• " + r for r in rules[:10]))
    return 0


def cmd_external(post_id, views, reactions):
    queue = load_json(QUEUE, [])
    for p in queue:
        if p["id"] == post_id:
            p.setdefault("analytics", {})["views"] = views
            p["analytics"]["reactions"] = reactions
            save_json(QUEUE, queue)
            print("метрики записаны:", post_id)
            return 0
    print("не найден:", post_id)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--rules", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--external", nargs=3, metavar=("ID", "VIEWS", "REACTIONS"))
    args = ap.parse_args()
    if args.report:
        return cmd_report()
    if args.rules:
        return cmd_rules()
    if args.stats:
        return cmd_stats()
    if args.external:
        return cmd_external(args.external[0], int(args.external[1]), int(args.external[2]))
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
