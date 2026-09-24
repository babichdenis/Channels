#!/bin/bash
# news_run.sh — один проход новостного конвейера (для launchd/cron)
cd /Users/Denis/channels || exit 1
/usr/bin/python3 news_conveyor.py --ingest
/usr/bin/python3 news_conveyor.py --clusters
/usr/bin/python3 news_conveyor.py --adapt 2
