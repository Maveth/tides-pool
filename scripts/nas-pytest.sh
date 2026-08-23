#!/bin/bash
set -euo pipefail
cd /app
pip install -e ".[dev]" -q
pytest -q
