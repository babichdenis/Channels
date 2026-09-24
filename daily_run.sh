#!/bin/bash
# daily_run.sh — ежедневный поток: сбор источников → адаптации → discovery → seed
cd /Users/Denis/channels || exit 1
/usr/bin/python3 conveyor.py --ingest >> /tmp/daily.log 2>&1
/usr/bin/python3 conveyor.py --adapt 3 --auto >> /tmp/daily.log 2>&1
/usr/bin/python3 discovery.py --collect >> /tmp/daily.log 2>&1
/usr/bin/python3 discovery.py --extract 4 >> /tmp/daily.log 2>&1
/usr/bin/python3 discovery.py --verify 3 >> /tmp/daily.log 2>&1
/usr/bin/python3 discovery.py --seeds >> /tmp/daily.log 2>&1
/usr/bin/python3 competitors.py --collect >> /tmp/daily.log 2>&1
echo "$(date '+%Y-%m-%d %H:%M') поток выполнен" >> /tmp/daily.log
