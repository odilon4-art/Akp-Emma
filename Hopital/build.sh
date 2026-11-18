#!/usr/bin/env bash
# Linux/macOS
set -e
python3 -m pip install -r requirements.txt
python3 -m PyInstaller --noconfirm --onefile --windowed --name OrdonnanceMobile --icon icon.ico --add-data "app:app" main.py
echo "OK : dist/OrdonnanceMobile"
