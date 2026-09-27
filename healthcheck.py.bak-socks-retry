#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcheck.py — сторож системы каналов: модели, агенты, свежесть данных, туннель.

Запускается launchd каждые 10 минут. При проблемах пишет алерт в Telegram (админу).
Повторный алерт по той же проблеме — не чаще раза в 6 часов; при восстановлении — «всё в норме».
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def http_json(url, payload=None, headers=None, timeout=15):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def age_min(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 60.0
    except OSError:
        return 10 ** 6


def main():
    cfg = load(os.path.join(HERE, "config.json"), {}) or {}
    admin = str(cfg.get("admin_chat_id") or "")
    env = {}
    try:
        for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    token = env.get("TG_TOKEN_CHANNELS", "")

    problems = []

    # 1) GLM-бридж: статус и лёгкий запрос
    bridge_up = False
    try:
        h = http_json("http://127.0.0.1:3001/health", timeout=8)
        bridge_up = True
        if not h.get("healthy"):
            problems.append("GLM-бридж: нездоров (%s)" % str(h)[:100])
        elif isinstance(h.get("tokenCount"), int) and h["tokenCount"] < 40:
            problems.append("GLM-бридж: мало токенов (%d)" % h["tokenCount"])
    except Exception as exc:
        problems.append("GLM-бридж недоступен (%s)" % type(exc).__name__)
    if bridge_up:
        try:
            r = http_json("http://127.0.0.1:3001/v1/chat/completions",
                          {"model": "glm-5.3", "messages": [{"role": "user", "content": "ping"}],
                           "max_tokens": 5, "stream": False},
                          {"Authorization": "Bearer glm-local", "Content-Type": "application/json"},
                          timeout=60)
            if not (r.get("choices") or [{}])[0].get("message", {}).get("content"):
                problems.append("GLM-бридж: пустой ответ на ping")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            low = body.lower()
            if "capacity" in low or "concurrency" in low or "rate" in low:
                print("(модель временно занята на Z.AI — не считаю сбоем)", flush=True)
            else:
                problems.append("GLM-бридж: ping HTTP %d (%s)" % (exc.code, body[:80]))
        except Exception as exc:
            problems.append("GLM-бридж: ошибка на ping (%s)" % type(exc).__name__)

    # 2) DeepRouter (модель контента)
    try:
        r2 = http_json("http://127.0.0.1:3000/v1/chat/completions",
                       {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 5, "stream": False},
                       {"Authorization": "Bearer deeprouter-local", "Content-Type": "application/json"},
                       timeout=60)
        if not (r2.get("choices") or [{}])[0].get("message", {}).get("content"):
            problems.append("DeepRouter: пустой ответ на ping")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        low = body.lower()
        if "capacity" in low or "concurrency" in low or "rate" in low or "overload" in low:
            print("(DeepRouter временно занят — не считаю сбоем)", flush=True)
        else:
            problems.append("DeepRouter: ping HTTP %d (%s)" % (exc.code, body[:80]))
    except Exception as exc:
        problems.append("DeepRouter: ошибка (%s)" % type(exc).__name__)

    # 3) ключевые агенты запущены
    try:
        procs = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=30).stdout
        for svc in ("com.denis.approval-bot", "com.denis.channels-scheduler", "com.denis.glm-rotator",
                    "com.denis.glm-watchdog", "com.denis.tg-socks"):
            line = next((l for l in procs.splitlines() if svc in l), "")
            pid = (line.split() or ["-"])[0]
            if pid in ("-", ""):
                problems.append("агент не запущен: %s" % svc)
    except Exception:
        problems.append("не удалось проверить агентов")

    # 4) свежесть данных
    if age_min(os.path.join(HERE, "news.db")) > 45:
        problems.append("новости: news.db не обновлялась >45 мин")
    if os.path.exists(os.path.expanduser("~/tguserbot/watch.json")) and age_min("/tmp/tg_watch.log") > 95:
        problems.append("tg-watch: последний прогон >95 мин назад")
    if age_min("/tmp/daily.log") > 26 * 60:
        problems.append("контент-агент: не запускался >26 ч")
    if age_min("/tmp/git_backup_ok") > 26 * 60:
        problems.append("git-бэкап: не запускался >26 ч")

    # 5) SOCKS-туннель до Telegram
    try:
        chk = subprocess.run(["curl", "-s", "--socks5-hostname", "127.0.0.1:1080", "-m", "12",
                              "-o", "/dev/null", "-w", "%{http_code}", "https://api.telegram.org"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        if chk == "000":
            problems.append("SOCKS-туннель до Telegram не работает")
    except Exception:
        problems.append("SOCKS-туннель: проверка не удалась")

    # 6) конвейер новостей не молчит: если после 14:00 за сегодня не было ни одного поста
    try:
        import datetime as _dt
        q = load(os.path.join(HERE, "queue.json"), []) or []
        days = []
        for p in q:
            if p.get("channel") == "ai_news":
                m2 = re.match(r"an-(\d{4}-\d{2}-\d{2})", p.get("id") or "")
                if m2:
                    days.append(m2.group(1))
        if days:
            now = _dt.datetime.now()
            last_day = max(days)
            if now.hour >= 14 and last_day != now.strftime("%Y-%m-%d"):
                problems.append("новости: сегодня ещё ни одного поста (последний %s)" % last_day)
    except Exception:
        pass

    # 7) VK-токен жив?
    try:
        vk_path = os.path.expanduser("~/secrets/vk_user_token")
        if os.path.exists(vk_path):
            vk_tok = open(vk_path, encoding="utf-8").read().strip()
            if vk_tok:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                url_vk = ("https://api.vk.com/method/users.get?access_token=%s&v=5.199"
                          % urllib.parse.quote(vk_tok))
                with opener.open(url_vk, timeout=15) as resp_vk:
                    vk_data = json.loads(resp_vk.read().decode("utf-8", "ignore"))
                if vk_data.get("error"):
                    vk_link = ("https://oauth.vk.com/authorize?client_id=52149278&display=mobile"
                               "&redirect_uri=https://oauth.vk.com/blank.html&scope=wall,groups,video"
                               "&response_type=token&v=5.199")
                    problems.append("VK-токен истёк. Обновление: открой %s , скопируй адрес из браузера "
                                    "и пришли боту." % vk_link)
    except Exception:
        pass

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print("%s | проблем: %d%s" % (ts, len(problems), (" | " + "; ".join(problems)) if problems else ""),
          flush=True)

    # алерты с антиспамом
    state_path = "/tmp/health_state.json"
    st = load(state_path, {}) or {}
    sig = "; ".join(problems)
    if problems:
        if sig != st.get("last_sig") or (time.time() - st.get("last_alert", 0)) > 6 * 3600:
            if token and admin:
                text = "⚠️ Сбой в системе каналов (%s):\n" % ts + "\n".join("• " + p for p in problems)
                try:
                    req = urllib.request.Request(
                        "https://api.telegram.org/bot%s/sendMessage" % token,
                        data=json.dumps({"chat_id": admin, "text": text[:3500]}).encode(),
                        headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=30)
                except Exception as exc:
                    print("алерт не ушёл: %s" % type(exc).__name__, flush=True)
            st["last_alert"] = time.time()
            st["last_sig"] = sig
    else:
        if st.get("last_sig"):
            if token and admin:
                try:
                    req = urllib.request.Request(
                        "https://api.telegram.org/bot%s/sendMessage" % token,
                        data=json.dumps({"chat_id": admin,
                                         "text": "✅ Система каналов в норме — сбои устранены (%s)" % ts}).encode(),
                        headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=30)
                except Exception:
                    pass
            st["last_sig"] = ""
    json.dump(st, open(state_path, "w", encoding="utf-8"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
