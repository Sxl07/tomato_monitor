"""Root conftest.py — ensures project root is on sys.path for all tests."""
import sys
from pathlib import Path

# Add project root to sys.path so that `from src.domain...` imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent))
