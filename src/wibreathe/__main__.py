"""Entrypoint for `python -m wibreathe` execution."""
import sys
from wibreathe.cli import main

if __name__ == "__main__":
    sys.exit(main())
