#!/bin/bash
# Iris — Stable Start Script
# Runs without debug mode so it doesn't crash on file changes
cd ~/image_library
pkill -f "python3.*app.py" 2>/dev/null
sleep 1
~/prometheus_venv/bin/python3 -c "
from app import app
from database import init_db
init_db()
app.run(host='0.0.0.0', port=5050, debug=False)
" &
echo "Iris running on http://localhost:5050 (PID: $!)"
