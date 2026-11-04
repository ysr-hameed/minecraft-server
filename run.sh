#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python -m pip install --break-system-packages -r requirements.txt
exec python app.py
