#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""approval_bot.py — утверждение постов через Telegram-бота (НейроСигнал и каналы).

Кнопки под постом:
  ✅ Опубликовать — пост уходит в канал сразу (через минуту)
  ❌ Отклонить — пост не публикуется
  ♻️ Перегенерировать — бот переписывает текст и присылает заново

Команды:
  /start — меню
  /queue — очередь на утверждение
  /edit <id> — заменить текст поста (следующим сообщением)
  /delete <id> — удалить пост из очереди
  /monitoring — статистика по каналам

Запуск: python3 approval_bot.py   (токен из .env: TG_TOKEN_CHANNELS)
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "queue.json")
CONFIG = os.path.join(HERE, "config.json")
STATE = os.path.join(HERE, "approval_state.json")
LOG = os.path.join(HERE, "approval.log")
MSK_TZ = ZoneInfo("Europe/Moscow")
CHANNEL_LINK_RE = re.compile(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,32})(?![A-Za-z0-9_/])")


def load_dotenv():
    try:
        with open(os.path.join(HERE, ".env"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


load_dotenv()
TOKEN = os.environ.get("TG_TOKEN_CHANNELS", "")
BRIDGE = "http://127.0.0.1:3001/v1/chat/completions"
BRIDGE_TOKEN = "glm-local"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def api(method, payload, timeout=70, files=None):
    url = "https://api.telegram.org/bot%s/%s" % (TOKEN, method)
    try:
        if files:
            boundary = "----appr%d" % int(time.time() * 1000)
            parts = []
            for key, val in payload.items():
                parts.append("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                             % (boundary, key, val))
            for key, (fname, blob) in files.items():
                parts.append("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                             "Content-Type: image/jpeg\r\n\r\n" % (boundary, key, fname))
            body = "".join(parts).encode("utf-8") + blob + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        else:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode("utf-8"))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8", "ignore"))
        except Exception:
            return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:150])}


def bridge_call(system, user, timeout=240):
    payload = {"model": "glm-5.3", "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.7, "max_tokens": 1200, "stream": False}
    req = urllib.request.Request(BRIDGE, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + BRIDGE_TOKEN,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def adapt_forward(text, channel):
    """Адаптирует пересланный текст в пост канала через промпт канала."""
    prompt_file = ("PROMPT_CHANNEL_NEURO_SECRETS.md" if channel == "neuro_secrets"
                   else "PROMPT_CHANNEL_WIZARD_PROMPTS.md")
    try:
        system = open(os.path.join(HERE, prompt_file), encoding="utf-8").read()
    except FileNotFoundError:
        system = "Ты редактор Telegram-канала. Перепиши текст в наш формат."
    reply = bridge_call(system, "Материал из другого канала:\n\n%s" % text[:4000])
    import re as _re
    m = _re.search(r"\{.*\}", _re.sub(r"^```(?:json)?|```$", "", reply, flags=_re.M).strip(), _re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def bridge_rewrite(text):
    payload = {
        "model": "glm-5.3",
        "messages": [
            {"role": "system", "content": "Перепиши пост для Telegram иначе: сохрани смысл и формат, "
                                          "сделай свежее и живее, те же хэштеги в конце. Верни только текст."},
            {"role": "user", "content": text}],
        "temperature": 0.9, "max_tokens": 900, "stream": False,
    }
    req = urllib.request.Request(BRIDGE, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + BRIDGE_TOKEN,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def download_tg_photo(file_id, dest_rel):
    """Скачивает фото из Telegram в posts/<dest_rel> (для пересланных постов)."""
    res = api("getFile", {"file_id": file_id})
    fp = (res.get("result") or {}).get("file_path") or ""
    if not fp:
        return ""
    url = "https://api.telegram.org/file/bot%s/%s" % (TOKEN, fp)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=90) as resp:
        blob = resp.read()
    if len(blob) < 3000:
        return ""
    os.makedirs(os.path.join(HERE, "posts"), exist_ok=True)
    with open(os.path.join(HERE, dest_rel), "wb") as fh:
        fh.write(blob)
    return dest_rel


def keyboard(post_id):
    return json.dumps({"inline_keyboard": [
        [{"text": "✅ Опубликовать сейчас", "callback_data": "approve_now:%s" % post_id},
         {"text": "🗓 В расписание", "callback_data": "approve_sched:%s" % post_id}],
        [{"text": "❌ Отклонить", "callback_data": "reject:%s" % post_id},
         {"text": "♻️ Перегенерировать", "callback_data": "regen:%s" % post_id}],
    ]}, ensure_ascii=False)


def menu_kb():
    """Постоянная клавиатура внизу чата — меню всегда под рукой."""
    return json.dumps({
        "keyboard": [[{"text": "📋 Очередь"}, {"text": "📊 Мониторинг"}]],
        "is_persistent": True, "resize_keyboard": True,
    }, ensure_ascii=False)


CHANNEL_LINKS = {}


def clean_tags(text):
    """Убираем хэштеги из текста — добавим один раз в конце."""
    text = re.sub(r"#[A-Za-zА-Яа-яЁё0-9_]{2,}", "", text or "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def post_caption(post, titles):
    title = titles.get(post.get("channel"), post.get("channel"))
    link = CHANNEL_LINKS.get(post.get("channel"))
    if link:
        title = '<a href="%s">%s</a>' % (link, title)
    when = (post.get("scheduled_at") or "")[11:16]
    head = "📝 <b>%s</b> | %s%s" % (title, post.get("rubric", ""), (" | %s" % when) if when else "")
    if post.get("origin"):
        head += " | источник: %s" % post["origin"]
    if post.get("source_url"):
        head += ' | <a href="%s">оригинал</a>' % post["source_url"]
    return "%s\n\n%s\n\n%s" % (head, clean_tags(post.get("text", "")), " ".join(post.get("hashtags") or []))


def send_for_approval(post, admin, titles):
    full = post_caption(post, titles)
    image = post.get("image")
    video = post.get("video")
    if video and os.path.exists(os.path.join(HERE, video)) and len(full) <= 1024:
        with open(os.path.join(HERE, video), "rb") as fh:
            blob = fh.read()
        return api("sendVideo", {"chat_id": admin, "caption": full,
                                 "parse_mode": "HTML", "reply_markup": keyboard(post["id"])},
                   files={"video": ("video.mp4", blob)}, timeout=240), "video"
    if image and os.path.exists(os.path.join(HERE, image)) and len(full) <= 1024:
        with open(os.path.join(HERE, image), "rb") as fh:
            blob = fh.read()
        return api("sendPhoto", {"chat_id": admin, "caption": full,
                                 "parse_mode": "HTML", "reply_markup": keyboard(post["id"])},
                   files={"photo": ("photo.jpg", blob)}), "photo"
    return api("sendMessage", {"chat_id": admin, "text": full[:4000],
                               "parse_mode": "HTML", "reply_markup": keyboard(post["id"])}), "text"


def mark_message(post, admin, status_text):
    """Помечает статус на карточке и оставляет кнопки навигации по очереди."""
    mid = post.get("_approval_message_id")
    if not mid:
        return
    try:
        cfg = load_json(CONFIG, {})
        title = (((cfg.get("channels") or {}).get(post.get("channel")) or {}).get("title")
                 or post.get("channel"))
        queue = load_json(QUEUE, [])
        left = sum(1 for p in queue if p.get("status") == "pending"
                   and p.get("channel") == post.get("channel"))
        nav = [[{"text": "📋 Ещё из «%s» (%d)" % (title, left), "callback_data": "qch:%s" % post.get("channel")}],
               [{"text": "📋 Вся очередь", "callback_data": "cmd:queue"}]]
    except Exception:
        nav = []
    payload = {"chat_id": admin, "message_id": mid,
               "reply_markup": json.dumps({"inline_keyboard": nav}, ensure_ascii=False)}
    kind = post.get("_approval_kind") or ("photo" if post.get("image") else "text")
    if kind in ("photo", "video"):
        payload["caption"] = (post_caption(post, {}) + "\n\n" + status_text)[:1024]
        res = api("editMessageCaption", payload)
    else:
        payload["text"] = (post_caption(post, {}) + "\n\n" + status_text)[:4000]
        res = api("editMessageText", payload)
    if not res.get("ok"):
        log("mark_message ошибка (%s): %s" % (mid, res.get("error") or res.get("description")))


def find_post(queue, post_id):
    for post in queue:
        if post["id"] == post_id:
            return post
    return None


CH_ICONS = {"neuro_secrets": "🥇", "wizard_prompts": "🥈", "neuro_work": "💼", "ai_news": "📰"}


def queue_menu_kb(queue, cfg):
    """Инлайн-клавиатура: кнопка на канал со счётчиком неразобранных постов."""
    chans = cfg.get("channels") or {}
    counts = {}
    for p in queue:
        if p.get("status") == "pending":
            counts[p.get("channel")] = counts.get(p.get("channel"), 0) + 1
    rows = []
    for key, c in chans.items():
        n = counts.get(key, 0)
        icon = CH_ICONS.get(key, "📁")
        rows.append([{"text": "%s %s — %d" % (icon, c.get("title", key), n), "callback_data": "qch:%s" % key}])
    total = sum(counts.get(key, 0) for key in chans)
    if total:
        rows.append([{"text": "📨 Все каналы — %d" % total, "callback_data": "qch:__all__"}])
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def send_queue(chat_id, edit_mid=None):
    """Меню очереди: кнопки по каналам с числом неразобранных постов."""
    queue = load_json(QUEUE, [])
    cfg = load_json(CONFIG, {})
    pend = [p for p in queue if p.get("status") == "pending"]
    text = "📋 <b>Очередь на утверждение</b> — %d\n\nВыбери канал:" % len(pend)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "reply_markup": queue_menu_kb(queue, cfg)}
    if edit_mid:
        payload["message_id"] = edit_mid
        return api("editMessageText", payload)
    res = api("sendMessage", payload)
    if res.get("ok"):
        mid = (res.get("result") or {}).get("message_id")
        state = load_json(STATE, {})
        old = state.get("menu_mid")
        if old:
            api("unpinChatMessage", {"chat_id": chat_id, "message_id": old})
        api("pinChatMessage", {"chat_id": chat_id, "message_id": mid, "disable_notification": True})
        state["menu_mid"] = mid
        save_json(STATE, state)
    return res


def send_channel_queue(chat_id, channel, edit_mid=None):
    """Список постов одного канала (или всех) + кнопка «прислать карточки»."""
    queue = load_json(QUEUE, [])
    cfg = load_json(CONFIG, {})
    titles = {k: v.get("title", k) for k, v in (cfg.get("channels") or {}).items()}
    if channel == "__all__":
        pend = [p for p in queue if p.get("status") == "pending"]
        title = "Все каналы"
    else:
        pend = [p for p in queue if p.get("status") == "pending" and p.get("channel") == channel]
        title = titles.get(channel, channel)
    lines = ["📋 <b>%s</b> — на утверждении: %d" % (title, len(pend)), ""]
    for p in pend[:40]:
        when = (p.get("scheduled_at") or "")[5:16].replace("T", " ")
        lines.append("• <code>%s</code> | %s | %s" % (p["id"], p.get("rubric", ""), when))
    if len(pend) > 40:
        lines.append("… и ещё %d" % (len(pend) - 40))
    rows = [[{"text": "📨 Прислать карточки (%d)" % len(pend), "callback_data": "qcards:%s" % channel},
             {"text": "◀️ К каналам", "callback_data": "cmd:queue"}]]
    payload = {"chat_id": chat_id, "text": "\n".join(lines)[:4000], "parse_mode": "HTML",
               "reply_markup": json.dumps({"inline_keyboard": rows}, ensure_ascii=False)}
    if edit_mid:
        payload["message_id"] = edit_mid
        return api("editMessageText", payload)
    return api("sendMessage", payload)


def resend_channel_cards(channel):
    """Заново присылает карточки утверждения по каналу (чтобы не искать в ленте)."""
    queue = load_json(QUEUE, [])
    cfg = load_json(CONFIG, {})
    admin = str(cfg.get("admin_chat_id") or "")
    titles = {k: v.get("title", k) for k, v in (cfg.get("channels") or {}).items()}
    if channel == "__all__":
        pend = [p for p in queue if p.get("status") == "pending"]
    else:
        pend = [p for p in queue if p.get("status") == "pending" and p.get("channel") == channel]
    sent = 0
    for post in pend[:30]:
        res, kind = send_for_approval(post, admin, titles)
        if res.get("ok"):
            post["_sent"] = True
            post["_approval_kind"] = kind
            post["_approval_message_id"] = (res.get("result") or {}).get("message_id")
            sent += 1
        time.sleep(0.5)
    if sent:
        save_json(QUEUE, queue)
    return sent


def send_monitoring(chat_id):
    queue = load_json(QUEUE, [])
    cfg = load_json(CONFIG, {})
    titles = {k: v.get("title", k) for k, v in (cfg.get("channels") or {}).items()}
    today = time.strftime("%Y-%m-%d")
    lines = ["📊 Мониторинг", ""]
    for ch, title in titles.items():
        posts = [p for p in queue if p.get("channel") == ch]
        pub_today = sum(1 for p in posts if p.get("status") == "published"
                        and (p.get("published_at") or "").startswith(today))
        pend = sum(1 for p in posts if p.get("status") == "pending")
        rej = sum(1 for p in posts if p.get("status") == "rejected")
        lines.append("• <b>%s</b>: опубликовано сегодня %d | ждут %d | отклонено %d" % (
            title, pub_today, pend, rej))
    lines.append("")
    lines.append("🤖 бридж: %s" % bridge_status())
    api("sendMessage", {"chat_id": chat_id, "text": "\n".join(lines)[:4000], "parse_mode": "HTML"})


def bridge_status():
    try:
        req = urllib.request.Request("http://127.0.0.1:3001/health")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        return "работает (токенов %s)" % data.get("tokenCount", "?")
    except Exception:
        return "не отвечает ⚠️"


def main():
    if not TOKEN:
        print("нет TG_TOKEN_CHANNELS в .env")
        return 1
    cfg = load_json(CONFIG, {})
    admin = str(cfg.get("admin_chat_id") or "")
    titles = {key: val.get("title", key) for key, val in (cfg.get("channels") or {}).items()}
    CHANNEL_LINKS.update({key: val.get("link", "") for key, val in (cfg.get("channels") or {}).items() if val.get("link")})
    state = load_json(STATE, {})
    offset = state.get("offset", 0)
    awaiting_edit = state.get("awaiting_edit") or ""
    chat_titles = {str(v.get("chat_id")): v.get("title", k) for k, v in (cfg.get("channels") or {}).items()
                   if v.get("chat_id")}
    print("бот утверждения запущен (админ %s)" % admin)
    log("бот утверждения запущен")
    last_count_check = 0.0
    while True:
        try:
            # 1) апдейты: кнопки и команды
            res = api("getUpdates", {"offset": offset, "timeout": 25,
                                     "allowed_updates": json.dumps(["message", "callback_query",
                                                                    "chat_member", "my_chat_member",
                                                                    "chat_join_request"])}, timeout=45)
            for upd in (res.get("result") or []):
                offset = max(offset, upd.get("update_id", 0) + 1)
                my = upd.get("my_chat_member")
                if my:
                    chat = my.get("chat") or {}
                    new = my.get("new_chat_member") or {}
                    status = new.get("status")
                    if status in ("administrator", "member"):
                        api("sendMessage", {"chat_id": admin, "text":
                            "ℹ️ Бот добавлен в «%s»\nid: <code>%s</code>\nтип: %s\nстатус: %s\n\n"
                            "Включи в группе «Одобрять новых участников» и привяжи её к каналу "
                            "(Управление → Обсуждение)." % (
                                chat.get("title"), chat.get("id"), chat.get("type"), status)})
                        log("my_chat_member: %s (%s) -> %s" % (chat.get("title"), chat.get("id"), status))
                    continue
                jr = upd.get("chat_join_request")
                if jr:
                    chat = jr.get("chat") or {}
                    user = jr.get("from") or {}
                    uid = user.get("id")
                    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) \
                        or user.get("username") or str(uid)
                    subscribed = False
                    sub_chat = ""
                    for ch_id, title in chat_titles.items():
                        res_m = api("getChatMember", {"chat_id": ch_id, "user_id": uid})
                        status = ((res_m.get("result") or {}).get("status") or "")
                        if status in ("member", "administrator", "creator"):
                            subscribed = True
                            sub_chat = title
                            break
                    if subscribed:
                        api("approveChatJoinRequest", {"chat_id": chat.get("id"), "user_id": uid})
                        api("sendMessage", {"chat_id": uid, "text":
                            "✅ Добро пожаловать в обсуждение «%s»! Пиши комментарии под постами." % sub_chat})
                        api("sendMessage", {"chat_id": admin, "text":
                            "🆕 В обсуждение вступил(а) %s (подписчик «%s»)" % (name, sub_chat)})
                        log("join_request одобрен: %s" % name)
                    else:
                        api("declineChatJoinRequest", {"chat_id": chat.get("id"), "user_id": uid})
                        api("sendMessage", {"chat_id": uid, "text":
                            "Чтобы комментировать, сначала подпишись на канал, потом нажми «Обсудить» ещё раз ✨"})
                        api("sendMessage", {"chat_id": admin, "text":
                            "🚫 Заявка отклонена (нет подписки): %s" % name})
                        log("join_request отклонён: %s" % name)
                    continue
                cm = upd.get("chat_member")
                if cm:
                    chat = cm.get("chat") or {}
                    new = cm.get("new_chat_member") or {}
                    old = cm.get("old_chat_member") or {}
                    if (new.get("status") in ("member", "administrator", "creator")
                            and old.get("status") in ("left", "kicked")):
                        user = new.get("user") or {}
                        name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) \
                            or user.get("username") or str(user.get("id"))
                        api("sendMessage", {"chat_id": admin, "text": "🆕 Новый участник в «%s»: %s" % (
                            chat.get("title"), name)})
                        log("новый участник: %s в %s" % (name, chat.get("title")))
                    continue
                cq = upd.get("callback_query")
                if cq:
                    data = cq.get("data", "")
                    action, _, post_id = data.partition(":")
                    if action == "to":
                        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "⏳ перерабатываю…"})
                        material = state.get("pending_text") or ""
                        if not material:
                            api("sendMessage", {"chat_id": admin, "text": "Материал не найден — пришли заново."})
                            continue
                        draft = adapt_forward(material, post_id)
                        if not draft or draft.get("skip"):
                            api("sendMessage", {"chat_id": admin, "text": "Не смог переработать (или не наш формат)."})
                            continue
                        when = (datetime.now(MSK_TZ) + timedelta(minutes=1)).replace(second=0, microsecond=0)
                        pid = "fw-%s-%s" % (when.strftime("%Y-%m-%d"), when.strftime("%H%M%S"))
                        image_path = ""
                        photo_id = state.get("pending_photo") or ""
                        if photo_id:
                            try:
                                image_path = download_tg_photo(photo_id, "posts/%s.jpg" % pid)
                            except Exception as exc:
                                log("фото пересланного: %s" % str(exc)[:120])
                        queue = load_json(QUEUE, [])
                        queue.append({
                            "id": pid, "channel": post_id, "scheduled_at": when.isoformat(),
                            "rubric": draft.get("rubric") or "Секрет дня",
                            "text": draft.get("text", "").strip(),
                            "hashtags": draft.get("hashtags") or [],
                            "image": image_path, "source_image": "",
                            "status": "pending", "check": {"evergreen": True, "editor": "forward"},
                            "origin": state.get("pending_origin") or "переслано вручную",
                        })
                        save_json(QUEUE, queue)
                        state["pending_text"] = ""
                        state["pending_photo"] = ""
                        state["pending_origin"] = ""
                        save_json(STATE, state)
                        api("sendMessage", {"chat_id": admin, "text": "✅ Черновик создан, сейчас придёт на утверждение."})
                        log("пересланный материал переработан: %s" % pid)
                        continue
                    if action == "cmd":
                        api("answerCallbackQuery", {"callback_query_id": cq["id"]})
                        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
                        if post_id == "queue":
                            send_queue(chat_id)
                        elif post_id == "monitoring":
                            send_monitoring(chat_id)
                        continue
                    if action in ("qch", "qcards"):
                        api("answerCallbackQuery", {"callback_query_id": cq["id"]})
                        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
                        mid = (cq.get("message") or {}).get("message_id")
                        if action == "qch":
                            send_channel_queue(chat_id, post_id, edit_mid=mid)
                        else:
                            n = resend_channel_cards(post_id)
                            api("sendMessage", {"chat_id": admin, "text": "📨 Отправил карточек: %d" % n})
                        continue
                    if action == "addsrc":
                        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "⏳ добавляю канал…"})
                        try:
                            out = subprocess.run([sys.executable, os.path.join(HERE, "source_add.py"), post_id],
                                                 capture_output=True, text=True, timeout=200)
                            report = (out.stdout or out.stderr or "").strip() or "не получилось"
                        except Exception as exc:
                            report = "ошибка: %s" % str(exc)[:120]
                        api("sendMessage", {"chat_id": admin, "text": report[:3500]})
                        log("источник (из пересланного): %s" % post_id)
                        continue
                    queue = load_json(QUEUE, [])
                    post = find_post(queue, post_id)
                    if not post:
                        msg = cq.get("message") or {}
                        mid = msg.get("message_id")
                        base = msg.get("text") or msg.get("caption") or ""
                        payload = {"chat_id": admin, "message_id": mid,
                                   "reply_markup": json.dumps({"inline_keyboard": []})}
                        if msg.get("caption"):
                            payload["caption"] = (base + "\n\n❌ Отклонено (уже не в очереди)")[:1024]
                            api("editMessageCaption", payload)
                        else:
                            payload["text"] = (base + "\n\n❌ Отклонено (уже не в очереди)")[:4000]
                            api("editMessageText", payload)
                        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "❌ отклонено"})
                        continue
                    post["_approval_message_id"] = (cq.get("message") or {}).get("message_id")
                    if action in ("approve", "approve_now", "approve_sched") and post.get("status") == "rejected":
                        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "⛔ уже отклонён (дубль)"})
                        mark_message(post, admin, "❌ Отклонено (дубль — кнопка устарела)")
                        continue
                    if action in ("approve", "approve_now", "approve_sched"):
                        post["status"] = "approved"
                        now = datetime.now(MSK_TZ)
                        if action == "approve_now":
                            post["scheduled_at"] = (now + timedelta(minutes=1)).replace(second=0, microsecond=0).isoformat()
                            toast, mark = "✅ публикую сейчас", "✅ Одобрено — публикую сейчас"
                        else:
                            try:
                                when = datetime.fromisoformat(post["scheduled_at"])
                            except Exception:
                                when = now - timedelta(minutes=1)
                            if when.tzinfo is None:
                                when = when.replace(tzinfo=MSK_TZ)
                            if when <= now:
                                post["scheduled_at"] = (now + timedelta(minutes=1)).replace(second=0, microsecond=0).isoformat()
                            toast, mark = "🗓 оставил по расписанию", "🗓 Одобрено — по расписанию (%s)" % post["scheduled_at"][11:16]
                        save_json(QUEUE, queue)
                        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": toast})
                        mark_message(post, admin, mark)
                        log("одобрен (%s): %s" % (action, post_id))
                    elif action == "reject":
                        post["status"] = "rejected"
                        save_json(QUEUE, queue)
                        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "❌ отклонено"})
                        mark_message(post, admin, "❌ Отклонено")
                        log("отклонён: %s" % post_id)
                    elif action == "regen":
                        api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "♻️ переписываю…"})
                        try:
                            new_text = bridge_rewrite(post.get("text", ""))
                        except Exception as exc:
                            new_text = ""
                            log("regen ошибка: %s" % str(exc)[:120])
                        if new_text:
                            post["text"] = new_text
                            post["_sent"] = False
                            save_json(QUEUE, queue)
                            mark_message(post, admin, "♻️ Перегенерирован — смотри новую версию ниже")
                            log("перегенерирован: %s" % post_id)
                        else:
                            api("sendMessage", {"chat_id": admin, "text": "⚠️ Не смог перегенерировать %s" % post_id})
                    continue
                msg = upd.get("message")
                if not msg:
                    continue
                chat_id = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or msg.get("caption") or "").strip()
                if str(chat_id) != admin:
                    continue
                if awaiting_edit and not text.startswith("/") and text not in ("📋 Очередь", "📊 Мониторинг"):
                    queue = load_json(QUEUE, [])
                    post = find_post(queue, awaiting_edit)
                    if post:
                        post["text"] = text
                        post["_sent"] = False
                        save_json(QUEUE, queue)
                        api("sendMessage", {"chat_id": chat_id, "text": "✏️ Текст обновлён: %s" % awaiting_edit})
                        log("отредактирован: %s" % awaiting_edit)
                    awaiting_edit = ""
                    continue
                m_tok = re.search(r"access_token=([A-Za-z0-9._\-]+)", text)
                if m_tok and len(text) > 60:
                    vk_tok = m_tok.group(1)
                    ok = False
                    try:
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        req = urllib.request.Request(
                            "https://api.vk.com/method/users.get?access_token=%s&v=5.199"
                            % urllib.parse.quote(vk_tok))
                        with opener.open(req, timeout=20) as resp:
                            vd = json.loads(resp.read().decode("utf-8", "ignore"))
                        ok = not vd.get("error")
                        err = (vd.get("error") or {}).get("error_msg", "")
                    except Exception as exc:
                        err = type(exc).__name__
                    if ok:
                        sec = os.path.expanduser("~/secrets")
                        os.makedirs(sec, exist_ok=True)
                        with open(os.path.join(sec, "vk_user_token"), "w", encoding="utf-8") as fh:
                            fh.write(vk_tok)
                        os.chmod(os.path.join(sec, "vk_user_token"), 0o600)
                        api("sendMessage", {"chat_id": chat_id, "text": "✅ VK-токен обновлён — VK-сбор продолжается."})
                        log("VK-токен обновлён из сообщения")
                    else:
                        api("sendMessage", {"chat_id": chat_id, "text": "⚠️ VK-токен не принят: %s" % str(err)[:100]})
                    continue
                m_code = re.search(r"[?&#]code=([A-Za-z0-9._\-]{10,})", text)
                if m_code:
                    m_dev = re.search(r"[?&#]device_id=([A-Za-z0-9._\-]+)", text)
                    try:
                        pk = load_json(os.path.expanduser("~/secrets/vk_pkce.json"), {}) or {}
                        secret = ""
                        try:
                            secret = open(os.path.expanduser("~/secrets/vk_app_secret"), encoding="utf-8").read().strip()
                        except OSError:
                            pass
                        payload = urllib.parse.urlencode({
                            "grant_type": "authorization_code",
                            "code": m_code.group(1),
                            "code_verifier": pk.get("verifier") or "",
                            "client_id": "52149278",
                            "client_secret": secret,
                            "redirect_uri": "https://oauth.vk.com/blank.html",
                            "device_id": m_dev.group(1) if m_dev else "",
                        }).encode()
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        req = urllib.request.Request(
                            "https://id.vk.ru/oauth2/auth", data=payload,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
                        with opener.open(req, timeout=30) as resp:
                            td = json.loads(resp.read().decode("utf-8", "ignore"))
                        if td.get("access_token"):
                            sec = os.path.expanduser("~/secrets")
                            with open(os.path.join(sec, "vk_user_token"), "w", encoding="utf-8") as fh:
                                fh.write(td["access_token"])
                            if td.get("refresh_token"):
                                with open(os.path.join(sec, "vk_refresh_token"), "w", encoding="utf-8") as fh:
                                    fh.write(td["refresh_token"])
                            api("sendMessage", {"chat_id": chat_id,
                                                "text": "✅ VK подключён надолго (токен с авто-обновлением)."})
                            log("VK ID: токены получены")
                        else:
                            api("sendMessage", {"chat_id": chat_id,
                                                "text": "⚠️ Обмен кода не удался: %s" % str(td)[:200]})
                    except Exception as exc:
                        api("sendMessage", {"chat_id": chat_id, "text": "⚠️ Обмен кода: %s" % str(exc)[:120]})
                    continue
                inv = re.search(r"(?:https?://)?t\.me/\+[A-Za-z0-9_-]{8,}", text)
                if inv and not msg.get("photo") and not awaiting_edit and len(text) <= 300:
                    api("sendMessage", {"chat_id": chat_id, "text": "🔒 Закрытый канал — подписываюсь и забираю посты…"})
                    try:
                        out = subprocess.run([sys.executable, os.path.join(HERE, "source_add.py"), inv.group(0)],
                                             capture_output=True, text=True, timeout=900)
                        report = (out.stdout or out.stderr or "").strip() or "не получилось"
                    except Exception as exc:
                        report = "ошибка: %s" % str(exc)[:120]
                    api("sendMessage", {"chat_id": chat_id, "text": report[:3500]})
                    log("приватный канал: %s" % inv.group(0))
                    continue
                m_link = CHANNEL_LINK_RE.search(text)
                if m_link and not msg.get("photo") and not awaiting_edit and len(text) <= 300:
                    chname = m_link.group(1)
                    api("sendMessage", {"chat_id": chat_id, "text": "🔎 Смотрю канал @%s…" % chname})
                    try:
                        out = subprocess.run([sys.executable, os.path.join(HERE, "source_add.py"), chname],
                                             capture_output=True, text=True, timeout=200)
                        report = (out.stdout or out.stderr or "").strip() or "не получилось"
                    except Exception as exc:
                        report = "ошибка: %s" % str(exc)[:120]
                    api("sendMessage", {"chat_id": chat_id, "text": report[:3500]})
                    log("источник: %s" % chname)
                    continue
                if text and not text.startswith("/") and not awaiting_edit and (len(text) > 120 or bool(msg.get("photo"))):
                    fo = msg.get("forward_origin") or {}
                    fo_chat = fo.get("chat") or {}
                    origin = (fo_chat.get("title") or fo_chat.get("username")
                              or (fo.get("sender_user") or {}).get("first_name") or "")
                    state["pending_text"] = text[:4000]
                    state["pending_photo"] = ((msg.get("photo") or [{}])[-1].get("file_id") or "")
                    state["pending_origin"] = origin
                    state["pending_origin_user"] = fo_chat.get("username") or ""
                    save_json(STATE, state)
                    rows = [[{"text": "🔮 В НейроХитрости", "callback_data": "to:neuro_secrets"},
                             {"text": "🪄 В ПромптКлад", "callback_data": "to:wizard_prompts"}]]
                    if fo_chat.get("username"):
                        rows.append([{"text": "➕ Канал @%s — в источники" % fo_chat["username"],
                                      "callback_data": "addsrc:%s" % fo_chat["username"]}])
                    kb = json.dumps({"inline_keyboard": rows}, ensure_ascii=False)
                    api("sendMessage", {"chat_id": chat_id,
                                        "text": "📥 Получил материал%s. Куда его переработать?" % (
                                            (" (из «%s»)" % origin) if origin else ""),
                                        "reply_markup": kb})
                    continue
                if text in ("📋 Очередь", "📋 На утверждении"):
                    send_queue(chat_id)
                    continue
                if text == "📊 Мониторинг":
                    send_monitoring(chat_id)
                    continue
                if text.startswith("/start") or text.startswith("/menu"):
                    api("sendMessage", {"chat_id": chat_id,
                                        "text": "Я бот утверждения публикаций.\n\n"
                                                "/queue — очередь\n/edit &lt;id&gt; — заменить текст\n"
                                                "/delete &lt;id&gt; — удалить из очереди\n"
                                                "/monitoring — статистика\n/stats — цифры недели\n/report — последний отчёт"
                                                "\n\n📥 Можно переслать сюда пост из любого канала — "
                                                "переработаю в черновик с картинкой.",
                                        "parse_mode": "HTML", "reply_markup": menu_kb()})
                    send_queue(chat_id)
                elif text.startswith("/queue"):
                    send_queue(chat_id)
                elif text.startswith("/monitoring"):
                    send_monitoring(chat_id)
                elif text.startswith("/competitors"):
                    out = subprocess.run([sys.executable, os.path.join(HERE, "competitors.py"), "--stats"],
                                         capture_output=True, text=True, timeout=120).stdout
                    api("sendMessage", {"chat_id": chat_id, "text": "🕵️ Конкуренты (просмотры):\n" + (out[:3800] or "нет данных")})
                elif text.startswith("/stats") or text.startswith("/report"):
                    if text.startswith("/report"):
                        reports = sorted(f for f in os.listdir(os.path.join(HERE, "reports"))
                                         if f.startswith("report_")) if os.path.isdir(os.path.join(HERE, "reports")) else []
                        if reports:
                            body = open(os.path.join(HERE, "reports", reports[-1]), encoding="utf-8").read()
                            api("sendMessage", {"chat_id": chat_id, "text": body[:4000]})
                        else:
                            api("sendMessage", {"chat_id": chat_id, "text": "Отчётов пока нет — первый придёт в понедельник."})
                    else:
                        out = subprocess.run([sys.executable, os.path.join(HERE, "analytics.py"), "--stats"],
                                             capture_output=True, text=True, timeout=120).stdout
                        api("sendMessage", {"chat_id": chat_id, "text": out[:4000] or "нет данных"})
                elif text.startswith("/edit"):
                    parts = text.split()
                    if len(parts) > 1:
                        awaiting_edit = parts[1]
                        api("sendMessage", {"chat_id": chat_id,
                                            "text": "✏️ Пришли новый текст для %s одним сообщением." % awaiting_edit})
                    else:
                        api("sendMessage", {"chat_id": chat_id, "text": "Формат: /edit <id поста>"})
                elif text.startswith("/delete"):
                    parts = text.split()
                    if len(parts) > 1:
                        queue = load_json(QUEUE, [])
                        post = find_post(queue, parts[1])
                        if post:
                            post["status"] = "rejected"
                            save_json(QUEUE, queue)
                            api("sendMessage", {"chat_id": chat_id, "text": "🗑 Удалён из очереди: %s" % parts[1]})
                        else:
                            api("sendMessage", {"chat_id": chat_id, "text": "не найден: %s" % parts[1]})
                    else:
                        api("sendMessage", {"chat_id": chat_id, "text": "Формат: /delete <id поста>"})
            state["offset"] = offset
            state["awaiting_edit"] = awaiting_edit
            # раз в минуту — проверка числа подписчиков в каналах
            if time.time() - last_count_check > 60:
                last_count_check = time.time()
                counts = state.get("counts") or {}
                for ch_id, title in chat_titles.items():
                    res_cnt = api("getChatMemberCount", {"chat_id": ch_id})
                    cnt = res_cnt.get("result")
                    if isinstance(cnt, int):
                        prev = counts.get(ch_id)
                        if prev is not None and cnt != prev:
                            diff = cnt - prev
                            api("sendMessage", {"chat_id": admin,
                                                "text": "👤 %s в «%s»: %+d (теперь %d)" % (
                                                    "Новый подписчик" if diff > 0 else "Отписался",
                                                    title, diff, cnt)})
                            log("подписчики %s: %+d -> %d" % (title, diff, cnt))
                        counts[ch_id] = cnt
                state["counts"] = counts
            save_json(STATE, state)
            # 2) новые посты на утверждение
            queue = load_json(QUEUE, [])
            changed = False
            for post in queue:
                if post.get("status") != "pending" or post.get("_sent"):
                    continue
                res2, kind = send_for_approval(post, admin, titles)
                if res2.get("ok"):
                    post["_sent"] = True
                    post["_approval_kind"] = kind
                    post["_approval_message_id"] = (res2.get("result") or {}).get("message_id")
                    changed = True
                    log("отправлен на утверждение: %s" % post["id"])
            if changed:
                save_json(QUEUE, queue)
        except Exception as exc:
            log("цикл: %s: %s" % (type(exc).__name__, str(exc)[:150]))
            time.sleep(5)
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
