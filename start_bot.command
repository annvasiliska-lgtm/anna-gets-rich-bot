#!/bin/zsh
cd "$(dirname "$0")"

if pgrep -f "$PWD/bot.py" >/dev/null; then
  echo "Anna Gets Rich bot is already running."
  echo "You can close this window."
  exit 0
fi

source .venv/bin/activate
python bot.py
