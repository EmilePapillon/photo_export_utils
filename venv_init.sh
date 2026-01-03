#!/usr/bin/env bash
set -euo pipefail

VENV=".venv"

if [ ! -d "$VENV" ]; then
  echo "==> Creating virtual environment in $VENV"
  python3 -m venv "$VENV"
else
  echo "==> Virtual environment already exists: $VENV"
fi

echo "==> Activating virtual environment"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> Upgrading pip"
pip install --upgrade pip

echo "==> Installing dependencies"
pip install -r requirements.txt

echo
echo "✔ Environment ready"
echo "Activate later with: source $VENV/bin/activate"
echo "Run stats with: python extract_statistics.py --input-dir ... --output-dir ..."
