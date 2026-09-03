import sys
from pathlib import Path

# Allow `pytest` to run without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
