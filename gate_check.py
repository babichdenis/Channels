# -*- coding: utf-8 -*-
"""Точечная проверка: известные рекламные посты должны падаться/чиститься,
известные нормальные новости — не трогаться."""
import json
import os
import sys

sys.path.insert(0, "/Users/Denis/channels")
import adgate

queue = json.load(open("/Users/Denis/channels/queue.json", encoding="utf-8"))

# (метка, подстрока-маркер, ожидание)
CASES = [
    ("AD",  "Pirate Face",            "реклама/мусор"),
    ("AD",  "AIgrator",               "рефералка"),
    ("AD",  "NaraRouter",             "индонезийская реклама"),
    ("AD",  "StudyAI",                "курс-агрегатор"),
    ("AD",  "Syntx",                  "сервис-реф"),
    ("AD",  "lava",                   "личный канал с наборами"),
    ("AD",  "Experiential",            "халява-сервис"),
    ("OK",  "независимый совет математиков", "чистая новость"),
    ("OK",  "взломал",                "т.co — новость, не реклама"),
    ("OK",  "AX",                     "Google open source"),
    ("OK",  "StarCraft",              "бенчмарк, ссылки-источники"),
    ("OK",  "Jev вышел",              "наша новость про Jev"),
    ("OK",  "дискуссии",              "обычный текст"),
    ("OK",  "Алиса Лю",               "история собеседований"),
    ("OK",  "Сбербанк",               "обычная новость"),
    ("OK",  "регулирование",          "обычная новость"),
]

print("%-6s %-34s %-6s %-6s %-5s  %s" % ("ОЖИД", "МАРКЕР", "ВЕРД", "SCORE", "ФЛАГ", "ЧТО УБРАЛИ"))
print("-" * 118)
fails = 0
for expect, marker, note in CASES:
    hits = [p for p in queue if marker.lower() in (p.get("text") or "").lower()]
    if not hits:
        print("%-6s %-34s НЕ НАЙДЕН" % (expect, marker[:34]))
        continue
    p = hits[0]
    v, rep, cleaned = adgate.gate(p, adgate.load_cache())
    got = v
    good = (got == "drop") if expect == "AD" else (got in ("keep", "trim"))
    if not good:
        fails += 1
    why = []
    for lf in rep["links"]:
        if lf.get("strong"):
            why.append("ссылка:%s[%s]" % (lf["domain"], ",".join(lf["flags"][:2])))
    for ph in rep["phrases"][:3]:
        why.append("фраза:%r" % ph["phrase"][:30])
    if not why:
        why = ["убрано строк: %d" % rep.get("removed_lines", 0)]
    print("%-6s %-34s %-6s %-6d %-5s  %s" % (
        expect, marker[:34], got, rep["score"],
        "OK" if good else "ПРОМАХ", "; ".join(why)[:60]))
print()
print("промахов: %d из %d" % (fails, len(CASES)))
