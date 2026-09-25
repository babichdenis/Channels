#!/bin/bash
# news_run.sh — сбор/кластеризация новостей. Генерация приостановлена: «НейроСигнал» = копии из каналов.
cd /Users/Denis/channels || exit 1
/usr/bin/python3 news_conveyor.py --ingest
/usr/bin/python3 news_conveyor.py --clusters
