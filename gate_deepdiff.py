# -*- coding: utf-8 -*-
"""Сравнение статики и deep-режима (реальные запросы по ссылкам).

Задача: убедиться, что проверка «куда ведёт ссылка» не создаёт
ложных срабатываний на нормальных новостях. Печатаем только те посты,
где deep изменил вердикт, — их смотрим глазами.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/Users/Denis/channels")
import adgate

DEEP = os.environ.get("DEEP", "1") == "1"
USE_LLM = os.environ.get("LLM", "0") == "1"

queue = json.load(open("/Users/Denis/channels/queue.json", encoding="utf-8"))
pending = [p for p in queue if (p.get("status") or "pending") == "pending"]
print("pending: %d   deep=%s  llm=%s" % (len(pending), DEEP, USE_LLM))
print("=" * 100)

llm = adgate.llm_verdict if USE_LLM else None
t0 = time.time()
rows, changed = [], []
for p in pending:
    v0, r0, _ = adgate.gate(p, adgate.load_cache())
    v1, r1, _ = adgate.gate(p, adgate.load_cache(), deep=DEEP, llm=llm)
    rows.append((v0, v1))
    if v0 != v1:
        changed.append((p, v0, v1, r0, r1))

cnt = {}
for v0, v1 in rows:
    cnt[v0] = cnt.get(v0, 0) + 1
print("СТАТИКА : %s" % cnt)
cnt2 = {}
for v0, v1 in rows:
    cnt2[v1] = cnt2.get(v1, 0) + 1
print("DEEP    : %s" % cnt2)
print("время   : %.1f с" % (time.time() - t0))
print()

print("ПОСТЫ, ГДЕ DEEP ИЗМЕНИЛ ВЕРДИКТ: %d" % len(changed))
print("=" * 100)
for p, v0, v1, r0, r1 in changed:
    pid = p.get("post_id") or p.get("id") or "?"
    src = p.get("channel") or p.get("source") or "?"
    print("• %s | %s" % (pid, src))
    print("   %s -> %s   score %d -> %d" % (v0, v1, r0["score"], r1["score"]))
    for lf in r1["links"]:
        if lf.get("final_url") or lf.get("link_score"):
            print("   %s -> %s  score=%s  %s" % (
                lf["domain"], (lf.get("final_url") or "")[:50],
                lf.get("link_score"), ",".join(lf["flags"])[:70]))
    if r1.get("llm"):
        print("   LLM: %s" % r1["llm"])
    print("   %s" % (p.get("text") or "")[:150].replace("\n", " | "))
    print()
