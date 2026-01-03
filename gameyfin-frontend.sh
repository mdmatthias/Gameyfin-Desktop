#!/bin/bash
export LD_LIBRARY_PATH=/app/lib:$LD_LIBRARY_PATH
export PYTHONPATH=/app/share/gameyfin:$PYTHONPATH
exec python3 /app/share/gameyfin/gameyfin_qt.py "$@"
