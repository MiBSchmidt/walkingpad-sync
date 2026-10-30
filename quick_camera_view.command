#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
"$DIR/venv/bin/python3" "$DIR/quick_camera_view.py"

