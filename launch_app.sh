#!/bin/bash
# Start (or just open) TCG Catalog. Double-click the desktop shortcut, or run: ./launch_app.sh
cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" || exit 1
exec ./venv/bin/python launch.py
