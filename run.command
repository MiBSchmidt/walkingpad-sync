#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -d "$DIR/venv" ]; then
    echo "Fehler: Virtuelle Umgebung (venv) nicht gefunden."
    read -p "Drücke Enter zum Beenden..."
    exit 1
fi

"$DIR/venv/bin/python3" "$DIR/walkingpad_logger.py"
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "⚠️ Die App wurde unerwartet beendet (Exit-Code: $EXIT_CODE)."
    echo "Drücke Enter, um dieses Fenster zu schließen..."
    read -r
fi
