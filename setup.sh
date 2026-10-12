#!/usr/bin/env bash
# setup.sh — builds the voice API's Python venv (./venv) and checks ffmpeg.
# Needs Python 3.11 or 3.12 (googletrans does not run on 3.13+). Re-run it any time.
set -u
cd "$(dirname "$0")"

pick_python() {
  for c in python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      v=$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
      case "$v" in 3.10|3.11|3.12) echo "$c"; return 0;; esac
    fi
  done
  return 1
}

PY=$(pick_python) || {
  echo "[FAIL] no Python 3.10-3.12 found."
  echo "       Install Python 3.11 (Debian/Ubuntu: sudo apt install python3.11 python3.11-venv),"
  echo "       or install uv (https://docs.astral.sh/uv) and run:  uv venv --python 3.11 venv"
  exit 1
}
echo "[OK]   using $PY ($($PY -V))"

if [ ! -x venv/bin/python ]; then
  "$PY" -m venv venv || { echo "[FAIL] could not create venv (install the python venv package)"; exit 1; }
fi
venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1
if venv/bin/python -m pip install -q -r requirements.txt; then
  echo "[OK]   python packages installed"
else
  echo "[FAIL] pip install failed (see messages above)"; exit 1
fi

if command -v ffmpeg >/dev/null 2>&1; then
  echo "[OK]   ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"
else
  echo "[FAIL] ffmpeg not found. Install it: sudo apt install ffmpeg   (or set FFMPEG in config.py)"
  exit 1
fi

echo
echo "Setup done. Next:"
echo "  1. set API_TOKEN in config.py (see the comment there)"
echo "  2. run tests:   venv/bin/python -m unittest discover -s tests -v"
echo "  3. start:       venv/bin/python server.py"
