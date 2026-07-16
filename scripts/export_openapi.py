"""Exports the FastAPI-generated OpenAPI schema to `openapi.json` at the
repo root (M9.T1.2) — the "shippable OpenAPI spec" PRD §2 requires. Run
whenever a route or schema changes: `python scripts/export_openapi.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    app = create_app(Settings(database_path=":memory:"))
    spec = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
