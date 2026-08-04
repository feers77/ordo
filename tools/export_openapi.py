"""Exporta el schema OpenAPI de cada servicio a docs/api/openapi/<servicio>.json.

Uso: uv run python tools/export_openapi.py
El resultado versionado es el baseline del test de contrato en CI.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

SERVICES = ["gateway", "iam", "api", "jobs", "events", "render", "mcp"]
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for service in SERVICES:
        module = importlib.import_module(f"ordo_{service}.main")
        schema = module.app.openapi()
        out = OUT_DIR / f"{service}.json"
        out.write_text(json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"{service}: {out.relative_to(OUT_DIR.parent.parent.parent)}")


if __name__ == "__main__":
    main()
