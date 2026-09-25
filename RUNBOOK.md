# RUNBOOK — GLM-мост и Telegram-оркестр каналов (машина .2)

> **Машина:** MacBook-ProDen **192.168.1.2** (пользователь `Denis`, пароль `0987`).
> Здесь живёт всё: GLM-мост, конвейер каналов, бот, юзербот, VK-сбор.
> Доступ с Mac .7: `sshpass -p '0987' ssh Denis@192.168.1.2`
> Токен GitHub (для git-бэкапа): `~/.git-credentials` на .2. Репозиторий: https://github.com/babichdenis/Channels (приватный).

---

## 0. Смоук-проверка (30 секунд)

```bash
sshpass -p '0987' ssh Denis@192.168.1.2 '
  tail -2 /tmp/health.log;
  echo "агентов: $(launchctl list | grep -c com.denis.)";
  curl -s -m 5 http://127.0.0.1:3001/health'
```

Ожидаем: `проблем: 0`, агентов **16**, в health — `"healthy":true` и `tokenCount` > 40.

---

## 1. Карта системы

| Компонент | Что/где | Агент launchd | Лог |
|---|---|---|---|
| GLM-мост (zai-api) | screen `glm2`, порт 3001 | — (screen) | `/tmp/glm5.log` (большой! только truncate) |
| Автопополнение токенов | `~/self-glm-bridge/autofeed.sh` | `com.denis.glm-autofeed` (10 мин) | `~/self-glm-bridge/autofeed.log` |
| Ротатор прокси (:3128) | `~/glm-rotator/rotator.py` | `com.denis.glm-rotator` | `/tmp/glm_rotator.log` |
| Сторож GLM (direct/WARP, токены) | `~/glm-rotator/glm_watchdog.py` | `com.denis.glm-watchdog` | `/tmp/glm_watchdog.log` |
| Туннели до VPS | `13128→VPS:8888` (tinyproxy), `13129→VPS:1080` (WARP) | `com.denis.glm-tunnel` | `/tmp/tg_socks.log` / plist stderr |
| SOCKS для Telegram (:1080) | `ssh -D root@46.8.228.71` | `com.denis.tg-socks` | `/tmp/tg_socks.log` |
| Бот утверждения | `~/channels/approval_bot.py` | `com.denis.approval-bot` | `~/channels/approval.log` |
| Планировщик публикаций | `~/channels/scheduler.py` | `com.denis.channels-scheduler` | `~/channels/scheduler.log` |
| Новости (сбор) | `~/channels/news_conveyor.py` | `com.denis.news-conveyor` (15 мин) | `/tmp/news_conveyor.log` |
| Репосты (копии из каналов) | `~/channels/repost.py` | `com.denis.repost` (30 мин) | `/tmp/repost.log` |
| VK-сбор | `~/channels/vk_ingest.py` | `com.denis.vk-ingest` (30 мин) | `/tmp/vk_ingest.log` |
| Юзербот (история/наблюдение) | `~/tguserbot/` | `com.denis.tg-watch` (30 мин) | `/tmp/tg_watch.log` |
| Поиск каналов | `~/tguserbot/channel_finder.py` | `com.denis.channel-finder` (4 ч) | `/tmp/finder.log` |
| Утренний поток | `~/channels/daily_run.sh` | `com.denis.content-daily` (08:00) | `/tmp/daily.log` |
| Аналитика | `~/channels/analytics.py` | `com.denis.analytics-weekly` (Пн 10:00) | `/tmp/daily.log` |
| Сторож системы | `~/channels/healthcheck.py` | `com.denis.health` (10 мин) | `/tmp/health.log` |
| Git-бэкап | `~/channels/backup_git.sh` | `com.denis.git-backup` (03:30) | `/tmp/git_backup.log`, маркер `/tmp/git_backup_ok` |

**Секреты** (chmod 600, не в git): `~/secrets/` — `channels.env` (TG-токен бота), `glm-bridge.env`, `vk_user_token` / `vk_service_token` / `vk_app_secret` / `vk_pkce.json`; `~/tguserbot/config.json` (api_id/api_hash/phone), `~/tguserbot/user.session`.

---

## 2. GLM — симптомы и лечение

### 2.1 `tokenCount` падает/0, стримы рвутся, «captcha generation returned empty payload»
Пуст пул device-токенов.
```bash
cd ~/self-glm-bridge && bash autofeed.sh     # порог 120, батч 200; есть ретраи 3×60с
tail -20 autofeed.log
curl -s http://127.0.0.1:3001/health          # tokenCount должен вырасти
```
Сторож сам запускает автопополнение при `tokenCount < 40`. Коллектор Playwright бывает флаки — подождать прогон или запустить руками ещё раз.

### 2.2 Мост лежит (health не отвечает)
```bash
screen -S glm2 -X quit; pkill -f zai-api; sleep 2
screen -dmS glm2 bash -lc "cd /Users/Denis/self-glm-bridge && set -a && . ./.env && set +a && exec ./zai-api >> /tmp/glm5.log 2>&1"
sleep 4; curl -s http://127.0.0.1:3001/health
```

### 2.3 WAF: 403 — норма, 405 — блок
```bash
curl -x http://127.0.0.1:3128 -s -o /dev/null -w "%{http_code}\n" https://chat.z.ai
```
- `403` — ок; `405` — WAF-блок. Сторож сам переключает маршрут direct ↔ WARP (см. `/tmp/glm_watchdog.log`).
- Пнуть:
```bash
launchctl kickstart -k gui/$(id -u)/com.denis.glm-watchdog
launchctl kickstart -k gui/$(id -u)/com.denis.glm-rotator
```

### 2.4 Туннели/VPS
```bash
launchctl list | grep glm-tunnel
ssh -o BatchMode=yes root@46.8.228.71 'docker ps --format "{{.Names}}: {{.Status}}"'   # ждём warp healthy
```
Если warp не healthy: `ssh root@46.8.228.71 'docker restart warp'`.
Туннель сам перезапускается (цикл в plist); пнуть: `launchctl kickstart -k gui/$(id -u)/com.denis.glm-tunnel`.

### 2.5 `/tmp/glm5.log` раздулся (гигабайты)
Только truncate, не rm:
```bash
: > /tmp/glm5.log
```

---

## 3. Telegram-оркестр (каналы) — симптомы и лечение

### 3.0 Первая точка входа — сторож
```bash
tail -20 /tmp/health.log
HTTPS_PROXY=http://127.0.0.1:3128 python3 ~/channels/healthcheck.py   # принудительный прогон
```
Алерты приходят в бота (антиспам: не чаще 1 раза в 6 ч; при восстановлении — «✅ в норме»).

### 3.1 Бот не отвечает / ошибки
```bash
launchctl list | grep approval-bot
tail -20 ~/channels/approval.log
launchctl kickstart -k gui/$(id -u)/com.denis.approval-bot
```
Типовые причины: `NameError`/`UnboundLocalError` (править код), Telegram без прокси (в plist обязателен `HTTPS_PROXY=http://127.0.0.1:3128` при живом ротаторе/туннеле).

### 3.2 Карточки не приходят, хотя посты есть
```bash
python3 - <<'PY'
import json
q = json.load(open('/Users/Denis/channels/queue.json'))
stuck = [p for p in q if p.get('status') == 'pending' and not p.get('_sent')]
print('без карточки:', len(stuck))
PY
```
Бот сам рассылает pending-без-`_sent` каждые ~25 сек. Если зависло — перезапустить бота. Сбросить `_sent` у конкретного поста — правкой `queue.json` (аккуратно, только нужный пост) и рестартом бота.

### 3.3 Публикации не идут
```bash
tail -5 ~/channels/scheduler.log
launchctl kickstart -k gui/$(id -u)/com.denis.channels-scheduler
```
Проверить: статус `approved`, `scheduled_at <= now`, `chat_id` в `config.json`. Ошибка `chat_id не задан` — канал не в конфиге или планировщик не перечитал (рестарт).

### 3.4 Репосты не появляются
```bash
tail -20 /tmp/repost.log
cd ~/channels && python3 repost.py --dry --count 3    # что бы скопировал
```
- `нет каналов-источников` → `~/tguserbot/watch.json` пуст: добавить каналы (кинуть ссылку в бота) или `venv/bin/python channel_finder.py`.
- Все «пропуск» → классификация не работает: проверить GLM-мост (раздел 2).
- Лимит: 3 поста за прогон, каждые 30 минут; копирует с картинками/видео, внешние ссылки сохраняются.

### 3.5 VK-сбор встал
```bash
tail -5 /tmp/vk_ingest.log
```
- Токен истёк: сторож пришлёт в бот ссылку. **Обновление:** открыть ссылку → «Разрешить» → скопировать адрес из браузера → отправить **боту** (он сам запишет `~/secrets/vk_user_token` и ответит «✅»).
- Вручную: положить токен в `~/secrets/vk_user_token`, затем `python3 ~/channels/vk_ingest.py`.
- Список VK-зеркал: `~/channels/vk_sources.json`; пересобрать: `python3 ~/channels/collect_vk.py`.

### 3.6 Юзербот (tguserbot)
```bash
launchctl list | grep tg-socks
curl -s --socks5-hostname 127.0.0.1:1080 -m 10 -o /dev/null -w "%{http_code}\n" https://api.telegram.org
```
- `000` → туннель лежит: `launchctl kickstart -k gui/$(id -u)/com.denis.tg-socks` (± проверить VPS).
- Ошибки Telethon «very old message / security error» — мигание туннеля, обычно самовосстанавливается; при залипании — kickstart туннеля и `cd ~/tguserbot && venv/bin/python watch.py`.
- Сессия слетела → перелогин: `venv/bin/python login1.py` → присланный код → `venv/bin/python login2.py <код>` (при 2FA: `login3.py <пароль>`).

### 3.7 Поиск каналов
```bash
tail -10 /tmp/finder.log
launchctl kickstart -k gui/$(id -u)/com.denis.channel-finder
```
Сам ищет, добавляет в источники, подписывается (до 3 за прогон) и качает историю.

### 3.8 Утренний поток / аналитика / бэкап
```bash
launchctl kickstart -k gui/$(id -u)/com.denis.content-daily
launchctl kickstart -k gui/$(id -u)/com.denis.analytics-weekly
bash ~/channels/backup_git.sh && tail -3 /tmp/git_backup.err
```

### 3.9 Перезапустить все агенты каналов
```bash
for a in approval-bot channels-scheduler news-conveyor content-daily analytics-weekly repost vk-ingest tg-watch channel-finder health; do
  launchctl kickstart -k gui/$(id -u)/com.denis.$a
done
```

---

## 4. Красные линии

- **Не трогать чужие процессы на .2:** uvicorn (торговый бот) и screens `aigate` / `aiwatch` / `aitrader` / `deeprouter` / `ocserve`.
- Логи только truncate (`: > file`), не `rm`.
- Секреты — только в `~/secrets`; в git не коммитить.
- Правки кода: сначала рабочая копия на Mac .7 (`~/Documents/Default Project/channels`), затем `scp` на .2 и `py_compile`; после правки бота — `kickstart`.
- Ночью в 03:30 всё уходит в `github.com/babichdenis/Channels` — проверка свежих правок там.
