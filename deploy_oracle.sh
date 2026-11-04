#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
sudo apt update
sudo apt install -y python3 python3-pip curl unzip zip
if ! python3 -m pip install --break-system-packages -r requirements.txt; then
  python3 -m pip install -r requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
fi
printf '\nPanel is ready.\n'
printf 'Edit .env, then run: python3 app.py\n'
printf 'Open panel port 5000/TCP and Bedrock port 19132/UDP in the Oracle Security List.\n'
