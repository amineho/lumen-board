#!/bin/bash
cd "$(dirname "$0")"
(sleep 2; open http://localhost:8080) &
python3 tracker.py
