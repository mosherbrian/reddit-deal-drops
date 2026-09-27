#!/usr/bin/env bash
# run_drop.sh -- harvest Reddit deal chatter, commit and push the JSON drop.
# Designed for cron, e.g.:
#   0 9 * * * /home/YOU/reddit-deal-drop/run_drop.sh
set -euo pipefail
cd "$(dirname "$0")"

python3 reddit_deal_drop.py >> drop.log 2>&1

git add drops >> drop.log 2>&1
if git diff --cached --quiet; then
  echo "$(date -Is) no changes to push" >> drop.log
  exit 0
fi
git commit -m "daily deal drop" >> drop.log 2>&1
git push >> drop.log 2>&1
