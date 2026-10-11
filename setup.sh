#!/usr/bin/env bash
# Sets up linkinspector: Python packages, Chromium for JavaScript mode, and the filter lists.
# Gemma, the local AI, is optional and installed separately (see the last step).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo "linkinspector needs Python 3.11 or newer (set PYTHON=/path/to/python to choose one)." >&2
    exit 1
fi

echo "== 1/4 Python packages"
[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "== 2/4 Chromium for JavaScript mode (about 115 MB)"
if ! .venv/bin/playwright install chromium; then
    echo "Chromium didn't install. Static mode still works; see the README for system libraries." >&2
fi

echo "== 3/4 Filter lists (uBlock Origin's defaults, about 8 MB)"
.venv/bin/python app.py --update-filters || echo "Some lists didn't download; run '.venv/bin/python app.py --update-filters' later." >&2

echo "== 4/4 Gemma, the local AI (optional)"
MODEL=${LINKINSPECTOR_MODEL:-gemma4:e2b-it-qat}
if command -v ollama >/dev/null 2>&1 && ollama list 2>/dev/null | grep -q "^$MODEL"; then
    echo "Gemma ($MODEL) is ready."
elif command -v ollama >/dev/null 2>&1; then
    echo "Ollama is installed. Download Gemma (4.3 GB) with:  ollama pull $MODEL"
else
    echo "For AI explanations, install Ollama from https://ollama.com/download, then run:  ollama pull $MODEL"
    echo "Without it, linkinspector still works and shows its code findings."
fi

echo
echo "Done. Start with:  .venv/bin/python app.py https://home.test/"
