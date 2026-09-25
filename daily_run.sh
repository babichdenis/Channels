#!/bin/bash
# daily_run.sh — ежедневный поток: сбор источников, зеркал VK и сигналов.
# Генерация постов приостановлена: контент идёт копиями из каналов (repost.py/vk_ingest.py).
cd /Users/Denis/channels || exit 1
/usr/bin/python3 conveyor.py --ingest >> /tmp/daily.log 2>&1
/usr/bin/python3 collect_vk.py >> /tmp/daily.log 2>&1
/usr/bin/python3 discovery.py --collect >> /tmp/daily.log 2>&1
/usr/bin/python3 competitors.py --collect >> /tmp/daily.log 2>&1
echo "$(date '+%Y-%m-%d %H:%M') поток выполнен (режим копий; генерация на паузе)" >> /tmp/daily.log
