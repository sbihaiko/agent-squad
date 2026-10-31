#!/bin/bash
# Tuning table for one UTC window, from the loop state only: tune-report.sh <since> [until]
exec python3 "$(dirname "$0")/tune_report.py" "$@"
