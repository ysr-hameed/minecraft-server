#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
pkg update -y
pkg install -y python curl tar zip unzip
python -m pip install --break-system-packages -r requirements.txt
exec python app.py
