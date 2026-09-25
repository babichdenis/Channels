#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scheduler.py — публикация постов каналов по расписанию.

Очередь постов: queue.json (формат — см. PROMPTS.md, раздел 7).
Конфиг каналов:  config.json (рядом со скриптом).
Токены ботов:    в переменных окружения (в файлах не храним).

Команды:
    python3 scheduler.py --list              # показать очередь
    python3 scheduler.py --approve <id>      # одобрить пост
    python3 scheduler.py --publish-now <id>  # опубликовать сейчас
    python3 scheduler.py --once              # один проход (для cron)
    python3 scheduler.py                     # цикл (проверка каждые 30 сек)
    python3 scheduler.py --dry               # ничего не отправлять, только лог

Статусы: draft → approved → published (или rejected).
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "queue.json")
CONFIG = os.path.join(HERE, "config.json")
LOG = os.path.join(HERE, "scheduler.log")
MSK = ZoneInfo("Europe/Moscow")
REACTIONS = {"ai_news": "👍", "neuro_secrets": "❤️", "wizard_prompts": "🔥", "neuro_work": "💼"}


def load_dotenv():
    """Токены ботов из .env рядом со скриптом (в git не попадает)."""
    path = os.path.join(HERE, ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


load_dotenv()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def log(event, **fields):
    row = {"ts": dt.datetime.now(MSK).isoformat(timespec="seconds"), "event": event}
    row.update(fields)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def tg_call(token, method, payload):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def react(token, chat_id, message_id, emoji):
    """Ставим реакцию от бота — тогда панель реакций видна на сообщении."""
    if not message_id or not emoji:
        return
    tg_call(token, "setMessageReaction", {
        "chat_id": chat_id, "message_id": message_id,
        "reaction": json.dumps([{"type": "emoji", "emoji": emoji}])})


def publish(post, channels, dry=False):
    cfg = channels.get(post.get("channel")) or {}
    chat_id = cfg.get("chat_id")
    token = os.environ.get(cfg.get("token_env", ""), "")
    if not chat_id:
        return {"ok": False, "skip": True, "error": "chat_id не задан для %s" % post.get("channel")}
    if not token:
        return {"ok": False, "error": "нет токена для %s" % post.get("channel")}
    text = post.get("text", "")
    # убираем ВСЕ хэштеги из текста, чтобы не дублировать (они добавятся ниже)
    text = re.sub(r"#[A-Za-zА-Яа-яЁё0-9_]{2,}", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if post.get("hashtags"):
        text = text + "\n\n" + " ".join(post["hashtags"])
    image = post.get("image")
    video = post.get("video")
    if image and len(text) > 1024:
        image = None  # длинный текст не влезает в подпись — публикуем без картинки
    if video and len(text) > 1024:
        video = None
    if video and os.path.exists(os.path.join(HERE, video)):
        with open(os.path.join(HERE, video), "rb") as fh:
            vblob = fh.read()
        boundary = "----schedv%d" % int(time.time())
        parts = []
        for key, val in (("chat_id", chat_id), ("caption", text[:1024]), ("parse_mode", "HTML")):
            parts.append("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, key, val))
        parts.append("--%s\r\nContent-Disposition: form-data; name=\"video\"; filename=\"video.mp4\"\r\n"
                     "Content-Type: video/mp4\r\n\r\n" % boundary)
        body = "".join(parts).encode("utf-8") + vblob + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendVideo" % token, data=body,
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                res = json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception as exc:
            res = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    elif image and os.path.exists(os.path.join(HERE, image)):
        with open(os.path.join(HERE, image), "rb") as fh:
            files = {"photo": fh.read()}
        # multipart для sendPhoto
        boundary = "----sched%d" % int(time.time())
        parts = []
        for key, val in (("chat_id", chat_id), ("caption", text[:1024]), ("parse_mode", "HTML")):
            parts.append("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, key, val))
        parts.append("--%s\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"photo.jpg\"\r\n"
                     "Content-Type: image/jpeg\r\n\r\n" % boundary)
        body = "".join(parts).encode("utf-8") + files["photo"] + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendPhoto" % token, data=body,
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                res = json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception as exc:
            res = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    else:
        res = tg_call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML"})
    if res.get("ok"):
        react(token, chat_id, ((res.get("result") or {}).get("message_id")),
              REACTIONS.get(post.get("channel"), "👍"))
    return res


def due(queue, now):
    out = []
    for post in queue:
        if post.get("status") not in ("approved", "scheduled"):
            continue
        try:
            when = dt.datetime.fromisoformat(post["scheduled_at"])
        except Exception:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=MSK)
        if when <= now:
            out.append(post)
    return out


def run_once(queue, channels, dry=False):
    now = dt.datetime.now(MSK)
    sent = 0
    for post in due(queue, now):
        if dry:
            title = (channels.get(post.get("channel")) or {}).get("title", post.get("channel"))
            print("[dry] «%s» ← %s" % (title, post["id"]))
            continue
        res = publish(post, channels, dry=dry)
        title = (channels.get(post.get("channel")) or {}).get("title", post.get("channel"))
        if res.get("ok"):
            post["status"] = "published"
            post["published_at"] = now.isoformat(timespec="seconds")
            post["message_id"] = ((res.get("result") or {}).get("message_id"))
            sent += 1
            log("published", post_id=post["id"], channel=post.get("channel"), title=title)
            print("опубликован: %s → «%s»" % (post["id"], title))
        else:
            post["status"] = "approved"
            err = res.get("error") or res.get("description")
            if res.get("skip"):
                log("skip", post_id=post["id"], channel=post.get("channel"), error=err)
                print("пропуск: %s → «%s»: %s" % (post["id"], title, err))
                continue
            log("error", post_id=post["id"], channel=post.get("channel"), title=title, error=err)
            print("ОШИБКА: %s → «%s»: %s" % (post["id"], title, err))
            admin = (load_json(CONFIG, {}).get("admin_chat_id"))
            token = os.environ.get("TG_TOKEN_ADMIN", "")
            if admin and token:
                tg_call(token, "sendMessage", {"chat_id": admin,
                        "text": "⚠️ Ошибка публикации в «%s» (%s): %s" % (title, post["id"], err)})
    return sent


def main():
    ap = argparse.ArgumentParser(description="Шедулер публикаций каналов")
    ap.add_argument("--list", action="store_true", help="показать очередь")
    ap.add_argument("--approve", metavar="ID", help="одобрить пост")
    ap.add_argument("--publish-now", metavar="ID", help="опубликовать сейчас")
    ap.add_argument("--once", action="store_true", help="один проход")
    ap.add_argument("--dry", action="store_true", help="без отправки")
    ap.add_argument("--test", metavar="CHANNEL", help="тестовое сообщение в канал")
    args = ap.parse_args()

    queue = load_json(QUEUE, [])
    channels = (load_json(CONFIG, {}) or {}).get("channels", {})

    if args.test:
        cfg = channels.get(args.test) or {}
        res = publish({"channel": args.test, "text": "🔧 Тест: канал подключён, шедулер готов.",
                       "hashtags": []}, channels, dry=args.dry)
        if res.get("ok"):
            print("тест отправлен в «%s»" % cfg.get("title", args.test))
            return 0
        print("ОШИБКА:", res.get("error") or res.get("description"))
        return 1

    if args.list:
        titles = {key: cfg.get("title", key) for key, cfg in channels.items()}
        for post in queue:
            print("%-22s %-18s %-10s %s | %s" % (
                post["id"], titles.get(post.get("channel"), post.get("channel")),
                post.get("status"), post.get("scheduled_at"),
                (post.get("rubric") or "")[:30]))
        return 0

    if args.approve:
        for post in queue:
            if post["id"] == args.approve:
                post["status"] = "approved"
                save_json(QUEUE, queue)
                print("одобрен:", post["id"])
                return 0
        print("не найден:", args.approve)
        return 1

    if args.publish_now:
        for post in queue:
            if post["id"] == args.publish_now:
                res = publish(post, channels, dry=args.dry)
                if res.get("ok"):
                    post["status"] = "published"
                    post["published_at"] = dt.datetime.now(MSK).isoformat(timespec="seconds")
                    post["message_id"] = ((res.get("result") or {}).get("message_id"))
                    save_json(QUEUE, queue)
                    print("опубликован:", post["id"])
                    return 0
                print("ОШИБКА:", res.get("error") or res.get("description"))
                return 1
        print("не найден:", args.publish_now)
        return 1

    if args.once:
        queue = load_json(QUEUE, [])
        channels = (load_json(CONFIG, {}) or {}).get("channels", {})
        n = run_once(queue, channels, dry=args.dry)
        if not args.dry:
            save_json(QUEUE, queue)
        print("отправлено:", n)
        return 0

    print("шедулер запущен (Ctrl+C — выход)")
    while True:
        try:
            queue = load_json(QUEUE, [])  # перечитываем, чтобы не затереть новые посты
            channels = (load_json(CONFIG, {}) or {}).get("channels", {})  # конфиг может меняться без рестарта
            n = run_once(queue, channels, dry=args.dry)
            if n and not args.dry:
                save_json(QUEUE, queue)
        except Exception as exc:
            log("fatal", error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
