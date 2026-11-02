#!/bin/bash
sudo apt update && sudo apt install -y python3 python3-pip openjdk-17-jre-headless unzip
pip3 install -r requirements.txt
cp .env.example .env || true
echo 'Edit .env password then run: nohup python3 app.py &'
