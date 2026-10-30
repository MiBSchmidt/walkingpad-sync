#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -d "$DIR/venv" ]; then
    echo "Fehler: Virtuelle Umgebung (venv) nicht gefunden."
    read -p "Drücke Enter zum Beenden..."
    exit 1
fi

"$DIR/venv/bin/python3" "$DIR/test_ocr.py"

