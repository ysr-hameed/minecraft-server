#!/bin/bash
pkg install -y python openjdk-17
pip install -r requirements.txt
python app.py
