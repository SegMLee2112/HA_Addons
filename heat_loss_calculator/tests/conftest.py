import sys
from pathlib import Path

# Tests import the add-on as `src` (it runs as `python3 -m src` in /app)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
