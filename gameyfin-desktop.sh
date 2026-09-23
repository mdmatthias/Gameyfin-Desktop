#!/bin/bash
export PYTHONPATH=/app/share/gameyfin:$PYTHONPATH
exec python3 /app/share/gameyfin/gameyfin_qt.py "$@"
