#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_vk.py — ищет VK-зеркала наших каналов (поиск по названиям из watch.json)
и проверяет их контентом: остаются только группы, чьи посты подходят нашим каналам.

Токен: ~/secrets/vk_user_token (запросы напрямую — домашний IP).
Результат: ~/channels/vk_sources.json
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from conveyor import fetch  # noqa: E402

WATCH = os.path.expanduser("~/tguserbot/watch.json")
VKSRC = os.path.join(HERE, "vk_sources.json")
TOKEN_FILE = os.path.expanduser("~/secrets/vk_user_token")
BRIDGE = "http://127.0.0.1:3001/v1/chat/completions"
BRIDGE_TOKEN = "glm-local"
GENERIC = {"нейросети", "нейросеть", "нейросетей", "промпты", "промпт", "новости", "news", "ai",
           "искусственный", "интеллект", "machine", "learning", "data", "science", "боты", "бот",
           "чат", "gpt", "chatgpt", "tools", "инструменты", "обзор", "канал", "для", "жизни", "бизнеса"}


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def api(token, method, params):
    q = "&".join("%s=%s" % (k, v) for k, v in params.items())
    page = fetch("https://api.vk.com/method/%s?%s&access_token=%s&v=5.199" % (method, q, token),
                 None, timeout=25)
    return json.loads(page)


def tokens(text):
    return {w for w in re.findall(r"[a-zа-яё0-9]{4,}", (text or "").lower())}


def clean_query(title):
    t = re.sub(r"[^\w\s\-]+", " ", title, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def bridge(system, user, timeout=180):
    payload = {"model": "glm-5.3",
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "temperature": 0.3, "max_tokens": 600, "stream": False}
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
              "«НейроСигнал» (новости ИИ). Определи для каждого канала, чем он полезен, или none. "
              'Ответ — строго JSON-массив: [{"n": 1, "fits": "secrets|prompts|work|news|none", "note": "до 8 слов"}]')
    try:
        txt = bridge(system, "\n\n".join(parts))
    except Exception:
        return None
    m = re.search(r"\[.*\]", re.sub(r"```(?:json)?", "", txt), re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        return None
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


def strong_match(title, screen, vk_name):
    tnorm = re.sub(r"[^a-zа-яё0-9]", "", (title or "").lower())
    for x in (screen or "", vk_name or ""):
        xnorm = re.sub(r"[^a-zа-яё0-9]", "", (x or "").lower())
        if len(xnorm) >= 5 and (xnorm in tnorm or tnorm in xnorm):
            return True
    return False


def validate(token, screen):
    """Проверяет контентом: возвращает (fits, note) или ('', '')."""
    try:
        d = api(token, "wall.get", {"domain": screen, "count": "5"})
    except Exception:
        return "", ""
    if d.get("error"):
        return "", ""
    items = (d.get("response") or {}).get("items") or []
    texts = [(it.get("text") or "").strip() for it in items]
    texts = [t for t in texts if len(t) >= 80]
    if not texts:
        return "", ""
    res = classify_batch([(screen, "\n---\n".join(t[:400] for t in texts[:3]))])
    if res is None:
        return "?", "проверка не удалась"
    row = res.get(screen) or {}
    fits = str(row.get("fits") or "none").lower()
    note = str(row.get("note") or "")
    if fits == "none":
        return "", note
    return (fits if fits in ("secrets", "prompts", "work", "news") else "?"), note


def main():
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    except OSError:
        token = ""
    if not token:
        print("нет пользовательского токена (~/secrets/vk_user_token)")
        return 1
    watch = load(WATCH, []) or []
    data = load(VKSRC, {}) or {"groups": []}

    # 1) перепроверяем уже записанные группы
    kept = []
    for g in data.get("groups", []):
        if strong_match(g.get("title"), g.get("screen"), g.get("vk_name")):
            kept.append(g)
            print("= vk.com/%-18s оставляю (сильное совпадение имени)" % g.get("screen"))
            continue
        fits, note = validate(token, g.get("screen"))
        if fits:
            g["fits"] = fits
            g["note"] = note
            kept.append(g)
            print("= vk.com/%-18s оставляю (%s: %s)" % (g.get("screen"), fits, note[:40]))
        else:
            print("× vk.com/%-18s убираю (контент не по темам)" % g.get("screen"))
        time.sleep(1)
    data["groups"] = kept
    known = {(g.get("screen") or "").lower() for g in kept}

    # 2) ищем зеркала для каналов, которых ещё нет
    found_titles = {g.get("title") for g in kept}
    added = 0
    for w in watch:
        title = (w.get("title") or "").strip()
        if not title or title in found_titles:
            continue
        variants = [clean_query(title)]
        for seg in re.split(r"[|•·\-–—]", title):
            seg = clean_query(seg)
            if 4 <= len(seg) <= 40:
                variants.append(seg)
        best = None
        for q in variants[:3]:
            if len(q) < 3:
                continue
            try:
                d = api(token, "groups.search", {"q": urllib.parse.quote(q), "type": "group", "count": "7"})
            except Exception:
                break
            if d.get("error"):
                break
            mine = tokens(title)
            for it in ((d.get("response") or {}).get("items") or []):
                name = it.get("name") or ""
                shared = mine & tokens(name)
                non_gen = len(shared - GENERIC)
                score = non_gen * 2 + len(shared)
                if (non_gen >= 1 or len(shared) >= 2) and (best is None or score > best["score"]):
                    best = {"score": score, "screen": it.get("screen_name"), "vk_name": name}
            if best:
                break
            time.sleep(1)
        if not best or not best.get("screen"):
            continue
        if strong_match(title, best["screen"], best["vk_name"]):
            fits, note = "mixed", "совпадение имени"
        else:
            fits, note = validate(token, best["screen"])
            if not fits:
                print("– «%s» → vk.com/%s: контент не подошёл" % (title[:35], best["screen"]))
                continue
        data["groups"].append({"screen": best["screen"], "title": title,
                               "vk_name": best["vk_name"], "fits": fits, "note": note})
        added += 1
        print("+ «%s» → vk.com/%s (%s: %s)" % (title[:35], best["screen"], fits, note[:40]))
        time.sleep(1)
    json.dump(data, open(VKSRC, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("итог: оставлено %d, добавлено %d, всего %d" % (len(kept), added, len(data["groups"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
