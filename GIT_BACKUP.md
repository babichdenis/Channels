# Git-бэкап конвейера каналов

Репозиторий: https://github.com/babichdenis/Channels (приватный)

**Что сохраняется** (каждую ночь в 03:30, launchd-агент `com.denis.git-backup`):
- весь код, промпты, конфиги, документация;
- очередь `queue.json`;
- скрипты юзербота → `system/tguserbot/`, launchd-агенты → `system/launchd/`.

**Что НЕ в git:**
- секреты: `~/channels/.env`, `~/tguserbot/config.json`, `~/tguserbot/user.session`
  (лежат на .2 в `~/secrets/`, chmod 700 — и в рабочих местах);
- базы `content.db` / `news.db` и медиа `media/`, `posts/` — при необходимости
  бэкапятся отдельным ночным архивом (обсудить).

**Восстановление:** `git clone` → вернуть секреты из `~/secrets/` на .2 → поднять launchd-агентов.

**Машина-хост:** MacBook-ProDen (192.168.1.2) — она же держит токен GitHub (`~/.git-credentials`).
