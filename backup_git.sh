#!/bin/bash
# Ночной бэкап конвейера каналов в GitHub (текстовые данные; секреты/базы/медиа исключены)
date '+%F %T' > /tmp/git_backup_ok
cd /Users/Denis/channels || exit 1
mkdir -p system/tguserbot system/launchd
cp -f /Users/Denis/tguserbot/*.py system/tguserbot/ 2>/dev/null
cp -f /Users/Denis/Library/LaunchAgents/com.denis.*.plist system/launchd/ 2>/dev/null
git add -A
if git diff --cached --quiet; then
  exit 0
fi
git commit -m "backup: $(date '+%Y-%m-%d %H:%M')" -q
git push -q origin main 2>/tmp/git_backup.err
