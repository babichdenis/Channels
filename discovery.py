#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discovery.py — движок поиска и проверки идей (не выдумываем, а находим).

  --collect      собрать сигналы (HN с «пользовательскими» запросами + текущие источники)
  --extract N    извлечь use-case из сигналов через LLM (problem/ai_use_case/action/evidence)
  --verify N     проверить кандидатов (trust/evidence + официальные источники) → verified + seed
  --seeds        отправить verified-кандидатов в оркестратор (conveyor --seed)
  --stats        что накопилось

Хранилище: candidates.json
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conveyor import (fetch, simhash, hamming, to_signed, bridge_chat, parse_json_reply,
                      load_channel_configs, load_history, looks_garbage, HERE, load_json, save_json)

CAND = os.path.join(HERE, "candidates.json")
CONFIG = os.path.join(HERE, "sources.json")

AUDIENCE = """Целевая аудитория сети: женщины 25–45, НЕ программисты и не технари.
Их задачи: дом, семья, дети, красота, работа (офис/фриланс/самозанятость), тексты, документы,
фото, покупки, планирование, соцсети, творчество.
НЕ подходит: разработка, код, IT-инфраструктура, DevOps, нейросети как профессия — чужая аудитория."""

QUERIES = ["I use ChatGPT to write", "ChatGPT for resume", "ChatGPT meal plan",
           "AI to organize my", "ChatGPT for emails", "how do I", "I just discovered"]

EXTRACT_SYSTEM = """Ты — аналитик контент-фабрики. Тебе дают сырой сигнал (пост/заголовок/описание).

""" + AUDIENCE + """

Извлеки из него РЕАЛЬНЫЙ пользовательский сценарий использования AI ДЛЯ НАШЕЙ АУДИТОРИИ.
Если сценарий про разработку/код/IT — это не наша аудитория: {"is_signal": false, "audience_fit": 0}.
Верни строго JSON:
{"is_signal": true, "problem": "какую задачу решает человек", "ai_use_case": "что делает с AI",
 "action": "конкретное действие (что загрузить/попросить)", "evidence_type":
 "official_feature|official_documentation|real_user_report|demonstrated_workflow|expert_test|media_report|community_claim|unverified_claim",
 "trust_level": 0, "novelty": 0, "interesting": true, "audience_fit": 0-10}
Если это не сценарий (реклама, новость, общее рассуждение) — {"is_signal": false}.
trust_level: 5 = официальный источник/документация, 4 = надёжный источник + демонстрация,
3 = реальный пользовательский кейс, 2 = соцпост, 1 = пересказ, 0 = непроверенный AI-текст."""

VERIFY_SYSTEM = """Ты — верификатор возможностей AI. Дано: сценарий использования и официальные материалы
(блоги/документация OpenAI, Google, DeepMind, HuggingFace).
Верни строго JSON: {"verified": "true|partial|false", "checked_against": ["..."], "limits": ["..."],
 "reason": "коротко", "seed": {"core_idea": "...", "audience_problem": "...", "desired_result": "...", "category": "..."}}
verified = true если возможность подтверждается официально или очевидно воспроизводима;
partial — работает нестабильно/с оговорками; false — не подтверждается."""


def cmd_collect(cfg):
    cands = load_json(CAND, [])
    seen = {c.get("hash") for c in cands}
    new = 0
    # 1) HN с «пользовательскими» запросами
    for q in QUERIES:
        try:
            data = json.loads(fetch("https://hn.algolia.com/api/v1/search?query=%s&tags=story&hitsPerPage=10"
                                    % urllib.parse.quote(q), cfg.get("proxy"), timeout=25))
        except Exception as exc:
            print("✗ HN «%s»: %s" % (q, str(exc)[:50]))
            continue
        for hit in (data.get("hits") or []):
            title = (hit.get("title") or "").strip()
            if len(title) < 20:
                continue
            h = str(abs(simhash(title)))
            if h in seen:
                continue
            seen.add(h)
            cands.append({
                "hash": h, "title": title, "url": hit.get("url") or "",
                "source_type": "hacker_news", "source_url": "https://news.ycombinator.com/item?id=%s" % hit.get("objectID"),
                "points": hit.get("points") or 0, "status": "raw", "query": q,
            })
            new += 1
        time.sleep(0.5)
    save_json(CAND, cands)
    print("собрано новых сигналов: %d | всего: %d" % (new, len(cands)))
    return 0


def cmd_extract(cfg, count):
    cands = load_json(CAND, [])
    made = 0
    for c in cands:
        if made >= count:
            break
        if c.get("status") != "raw":
            continue
        try:
            reply = bridge_chat(cfg, EXTRACT_SYSTEM, "Сигнал: %s\nURL: %s" % (c.get("title"), c.get("url")))
        except Exception as exc:
            print("✗ %s" % str(exc)[:60])
            break
        data = parse_json_reply(reply)
        c["status"] = "extracted"
        if not data or not data.get("is_signal"):
            c["status"] = "not_signal"
            continue
        c.update({k: data.get(k) for k in ("problem", "ai_use_case", "action", "evidence_type",
                                           "trust_level", "novelty", "interesting", "audience_fit")})
        if int(data.get("audience_fit") or 0) < 6:
            c["status"] = "not_audience"
            print("· не наша аудитория: %s" % c["title"][:50])
            continue
        made += 1
        print("✓ %s → %s" % (c["title"][:40], (data.get("ai_use_case") or "")[:60]))
        time.sleep(1)
    save_json(CAND, cands)
    print("извлечено use-case: %d" % made)
    return 0


def cmd_verify(cfg, count):
    cands = load_json(CAND, [])
    # официальные материалы для проверки — из news.db
    official = []
    try:
        import sqlite3
        conn = sqlite3.connect(os.path.join(HERE, "news.db"))
        rows = conn.execute("SELECT source, title, text FROM news WHERE source IN "
                            "('openai_blog','google_ai','deepmind','huggingface') ORDER BY id DESC LIMIT 40").fetchall()
        official = ["%s: %s" % (r[0], r[1]) for r in rows]
    except Exception:
        pass
    made = 0
    for c in cands:
        if made >= count:
            break
        if c.get("status") != "extracted":
            continue
        user = "Сценарий: %s\nПроблема: %s\nДействие: %s\nОфициальные материалы:\n%s" % (
            c.get("ai_use_case"), c.get("problem"), c.get("action"), "\n".join(official[:25]) or "-")
        try:
            reply = bridge_chat(cfg, VERIFY_SYSTEM, user)
        except Exception as exc:
            print("✗ %s" % str(exc)[:60])
            break
        data = parse_json_reply(reply) or {}
        c["verified"] = (data.get("verified") or "partial").lower()
        c["checked_against"] = data.get("checked_against") or []
        c["limits"] = data.get("limits") or []
        c["seed"] = data.get("seed") or {}
        c["status"] = "verified" if c["verified"] in ("true", "partial") else "rejected"
        made += 1
        print("✓ verified=%s | %s" % (c["verified"], (c.get("ai_use_case") or "")[:60]))
        time.sleep(1)
    save_json(CAND, cands)
    print("проверено: %d" % made)
    return 0


def cmd_seeds(cfg):
    cands = load_json(CAND, [])
    seeds = [c for c in cands if c.get("status") == "verified" and c.get("seed")]
    if not seeds:
        print("нет verified-кандидатов с seed — сначала --verify")
        return 1
    seed = seeds[0]
    idea = seed["seed"].get("core_idea") or seed.get("ai_use_case")
    print("запускаю seed:", idea)
    import subprocess
    return subprocess.call([sys.executable, os.path.join(HERE, "conveyor.py"), "--seed", idea])


def cmd_stats(cfg):
    cands = load_json(CAND, [])
    from collections import Counter
    print(dict(Counter(c.get("status") for c in cands)))
    for c in cands[-5:]:
        if c.get("status") == "verified":
            print(" • verified=%s | %s" % (c.get("verified"), (c.get("ai_use_case") or c.get("title") or "")[:70]))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Discovery: поиск и проверка идей")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--extract", type=int, metavar="N")
    ap.add_argument("--verify", type=int, metavar="N")
    ap.add_argument("--seeds", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    cfg = load_json(CONFIG, {})
    if args.collect:
        return cmd_collect(cfg)
    if args.extract:
        return cmd_extract(cfg, args.extract)
    if args.verify:
        return cmd_verify(cfg, args.verify)
    if args.seeds:
        return cmd_seeds(cfg)
    if args.stats:
        return cmd_stats(cfg)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
