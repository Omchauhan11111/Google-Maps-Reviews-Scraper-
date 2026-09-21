"""WSGI entry point for local production serving."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from maps_review_extractor import create_app

app = create_app()
