#!/bin/bash
# Shoot every guide clip at 1080p / 30 fps into promo/footage (3 at a time: memory).
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe
run() { $PY tools/shoot.py "$1" --out promo/footage > "promo/footage/$1.log" 2>&1; }
mkdir -p promo/footage
run pedals_t1 & run steer_para & run steer_run & wait
run cams & run lookback & run pause & wait
run reset & run shift & run menu & wait
echo ALL DONE > promo/footage/_done.txt
