#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repost.py — копирует свежие посты из каналов-источников (userbot watch) в наши каналы.

Почти как есть: текст чистится от подписи и ссылок источника, картинка берётся из поста.
Канал назначения определяется по формату (секреты/промпты/работа/новости). Посты уходят в бот на утверждение.

Использование:
    python3 repost.py [--count 3] [--dry]
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from conveyor import simhash, hamming, to_signed  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
QUEUE = os.path.join(HERE, "queue.json")
STATE = os.path.join(HERE, "repost_state.json")
WATCH = os.path.expanduser("~/tguserbot/watch.json")
BRIDGE = "http://127.0.0.1:3001/v1/chat/completions"
BRIDGE_TOKEN = "glm-local"
CH_HASHTAGS = {
    "neuro_secrets": ["#нейросети", "#лайфхаки"],
    "wizard_prompts": ["#промпты", "#нейросети"],
    "neuro_work": ["#нейросети", "#работа"],
    "ai_news": ["#новости_ии"],
}
CLEAN_LINE = re.compile(r"(подпис(ывай|аться|ка|шись)|больше новостей|читайте|наш канал|"
                        r"телеграм[- ]канал|реклама|по рекламе|лучшее на|t\.me/)", re.I)
# чужие Telegram-каналы (ссылки и @упоминания) вырезаем, внешние сайты оставляем
TG_LINK = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/[A-Za-z0-9_/?+=.-]+", re.I)
TG_MENTION = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,32}")


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def bridge_call(system, user, timeout=120):
    payload = {"model": "glm-5.3",
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "temperature": 0.2, "max_tokens": 200, "stream": False}
    req = urllib.request.Request(BRIDGE, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + BRIDGE_TOKEN,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def classify(text):
    system = ("Ты — редактор сети Telegram-каналов про ИИ. Определи, в какой канал отдать этот пост: "
              "neuro_secrets («Нейро-секретики» — секреты, лайфхаки, инструменты и промпты для жизни: "
              "дом, красота, дети, отношения), "
              "wizard_prompts («ПромптКлад» — готовые промпты и разборы), "
              "neuro_work («Нейропомощник» — AI для работы: письма, документы, таблицы, автоматизация), "
              "ai_news («НейроСигнал» — новости ИИ: релизы моделей, компании, рынок). "
              'Ответ — строго JSON без пояснений: {"channel": "neuro_secrets|wizard_prompts|neuro_work|ai_news|skip"}')
    try:
        reply = bridge_call(system, text[:3000])
    except Exception:
        return ""
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        return ""
    try:
        return (json.loads(m.group(0)).get("channel") or "").strip().lower()
    except ValueError:
        return ""


def clean_source_text(text, source):
    """Убираем подписи, рекламу и ссылки/упоминания чужих TG-каналов; внешние ссылки остаются."""
    src = (source or "").lower()
    if src.startswith("ub_"):
        src = src[3:]
    src_word = re.compile(r"(?<![\wа-яё])%s(?![\wа-яё])" % re.escape(src)) if src else None
    out = []
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if not s:
            out.append("")
            continue
        if CLEAN_LINE.search(s) and len(s) < 220:
            continue
        low = s.lower()
        if src_word and (src_word.search(low) or ("@%s" % src) in low):
            continue  # строка с именем канала-источника (самореклама)
        s = TG_LINK.sub("", s)
        s = TG_MENTION.sub("", s).strip()
        s = s.strip(" ·—–-|🔗\t").strip()
        if len(s) < 5 or (s.endswith(":") and len(s) < 14):
            continue  # строка состояла только из ссылки/упоминания («Бот:» и т.п.)
        out.append(s)
    t = "\n".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def watched_keys():
    watch = load(WATCH, []) or []
    keys = set()
    for w in watch:
        for k in (w.get("key"), w.get("ref")):
            k = (k or "").strip().lstrip("@").lower()
            if k and re.match(r"^[a-z0-9_]{4,32}$", k):
                keys.add(k)
    return keys


def is_watched(source, keys):
    s = (source or "").lower()
    if s.startswith("vk_"):
        return True
    if s in keys:
        return True
    if s.startswith("ub_") and s[3:] in keys:
        return True
    return False


def main():
    dry = "--dry" in sys.argv
    cap = int(sys.argv[sys.argv.index("--count") + 1]) if "--count" in sys.argv else 3
    keys = watched_keys()
    if not keys:
        print("нет каналов-источников (watch.json пуст)")
        return 0
    st = load(STATE, {}) or {}
    recent = st.get("reposted", [])[-200:]
    q = load(QUEUE, []) or []
    seen = [to_signed(simhash(p.get("text") or "")) for p in q]
    seen += [int(r.get("sh") or 0) for r in recent]

    conn = sqlite3.connect(os.path.join(HERE, "content.db"), timeout=30)
    try:
        conn.execute("ALTER TABLE items ADD COLUMN video_local TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    rows = conn.execute("SELECT id, source, text, ts, image_local, video_local FROM items "
                        "WHERE used = 0 ORDER BY ts DESC LIMIT 300").fetchall()
    made = 0
    for iid, source, text, ts, image_local, video_local in rows:
        if made >= cap:
            break
        if not is_watched(source, keys):
            continue
        text = (text or "").strip()
        if len(text) < 60:
            conn.execute("UPDATE items SET used = 1 WHERE id = ?", (iid,))
            conn.commit()
            continue
        clean = clean_source_text(text, source)
        if len(clean) < 50:
            conn.execute("UPDATE items SET used = 1 WHERE id = ?", (iid,))
            conn.commit()
            continue
        sh = to_signed(simhash(clean))
        if any(hamming(sh, s) <= 8 for s in seen):
            conn.execute("UPDATE items SET used = 1 WHERE id = ?", (iid,))
            conn.commit()
            continue
        target = classify(clean)
        if target not in CH_HASHTAGS:
            conn.execute("UPDATE items SET used = 1 WHERE id = ?", (iid,))
            conn.commit()
            print("· пропуск (%s): %s" % (source, clean[:60].replace("\n", " ")))
            continue
        when = datetime.now(MSK) + timedelta(minutes=2)
        pid = "rp-%s-%s-%s" % (when.strftime("%Y-%m-%d"), when.strftime("%H%M%S"), iid)
        when = when.replace(second=0, microsecond=0)
        q2 = load(QUEUE, []) or []
        q2.append({"id": pid, "channel": target, "scheduled_at": when.isoformat(),
                   "rubric": "Репост", "text": clean, "hashtags": CH_HASHTAGS[target],
                   "image": image_local or "", "video": video_local or "", "source_image": "",
                   "status": "pending", "check": {"repost": True},
                   "origin": "%s (копия)" % source})
        if not dry:
            save(QUEUE, q2)
            seen.append(sh)
            recent.append({"sh": sh, "id": pid, "ts": ts})
            st["reposted"] = recent[-200:]
            save(STATE, st)
            conn.execute("UPDATE items SET used = 1 WHERE id = ?", (iid,))
            conn.commit()
        made += 1
        print("✓ %s → %s (%s, %d симв.)" % (pid, target, source, len(clean)))
    print("репостов создано:", made, "(dry)" if dry else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
