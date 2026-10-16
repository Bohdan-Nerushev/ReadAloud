"""
Entry point for the ReadAloud application.
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from src.main import main

if __name__ == "__main__":
    main()
