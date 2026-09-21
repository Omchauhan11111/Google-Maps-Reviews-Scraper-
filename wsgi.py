"""WSGI entry point for local and cloud production serving."""

import os
import sys
from pathlib import Path
from waitress import serve

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from maps_review_extractor import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting server on port {port}...")
    serve(app, host="0.0.0.0", port=port)

