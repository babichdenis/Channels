# -*- coding: utf-8 -*-
"""Сухой прогон гейта по очереди: сколько постов дойдёт, сколько убьётся."""
import json
import os
import sys

sys.path.insert(0, "/Users/Denis/channels")
import adgate

HERE = "/Users/Denis/channels"
Q = os.path.join(HERE, "queue.json")

queue = json.load(open(Q, encoding="utf-8"))
cache = adgate.load_cache()

rows = []
for p in queue:
    if p.get("status") != "pending":
        continue
    verdict, rep, cleaned = adgate.gate(p, cache)
    rows.append((p, verdict, rep, cleaned))

from collections import Counter
cnt = Counter(v for _, v, _, _ in rows)
print("=== ИТОГ по %d pending ===" % len(rows))
for k in ("keep", "trim", "drop"):
    print("  %-5s %d" % (k, cnt.get(k, 0)))
print()

print("=" * 100)
print("DROP — посты, которые не показываем вообще")
print("=" * 100)
for p, v, rep, _ in rows:
    if v != "drop":
        continue
    reasons = []
    for lf in rep["links"]:
        if lf["is_ad"]:
            reasons.append("ссылка:%s(%s)" % (lf["domain"], ",".join(lf["flags"][:2])))
    for ph in rep["phrases"][:3]:
        reasons.append("фраза:%r" % ph["phrase"][:34])
    print("• %s | %s" % (p["id"], p.get("origin", "")[:34]))
    print("   score=%d флагов=%d" % (rep["score"], len(reasons)))
    for r in reasons[:5]:
        print("     - %s" % r)
    print("   %s" % (p.get("text", "")[:150].replace("\n", " ")))
    print()

print("=" * 100)
print("TRIM — покажем с очисткой (первые 20)")
print("=" * 100)
n = 0
for p, v, rep, cleaned in rows:
    if v != "trim":
        continue
    n += 1
    if n > 20:
        break
    print("• %s | %s | score=%d" % (p["id"], p.get("origin", "")[:30], rep["score"]))
    print("   ЧТО УБРАЛИ:")
    for lf in rep["links"]:
        if lf["is_ad"]:
            print("     ссылка %s  [%s]" % (lf["domain"], ",".join(lf["flags"][:3])))
    for ph in rep["phrases"][:3]:
        print("     фраза %r" % ph["phrase"][:44])
    print("   ОСТАЛОСЬ: %s" % cleaned[:170].replace("\n", " "))
    print()
