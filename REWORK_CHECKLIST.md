# REWORK_CHECKLIST.md

## Перед запуском

- [ ] применить `content_guard.py`;
- [ ] применить patch;
- [ ] установить Pillow;
- [ ] запустить unit tests;
- [ ] очистить/проверить старую `queue.json`;
- [ ] проверить 20 старых репостов;
- [ ] проверить 20 новых репостов;
- [ ] проверить 20 новостей;
- [ ] проверить 20 изображений.

## Acceptance criteria

### Repost

0 Telegram-рекламных хвостов из 100 тестовых постов.

### Images

0 случаев crop.

Проверять:

```text
source width/height
=
saved width/height
```

для source-preserve media.

### News

У каждой опубликованной новости есть:

- event;
- actor;
- change;
- event_date;
- evidence;
- source_url.

### QC

При падении QC:

```text
MANUAL_REVIEW
```

а не PASS.

### Publish

Последний gate должен быть единственным местом, которое реально вызывает Telegram API.
