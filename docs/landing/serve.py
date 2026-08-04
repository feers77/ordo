#!/usr/bin/env python3
"""Servidor estático del sitio de documentación de ORDO.

Sirve `docs/landing/` en 0.0.0.0:8888 y expone los contratos OpenAPI
versionados (`docs/api/openapi/*.json`) bajo la ruta `/openapi/`.

Solo biblioteca estándar: sin dependencias, sin red, sin CDNs. El sitio
funciona íntegro estando offline.

Uso:
    python3 docs/landing/serve.py [--host HOST] [--port PORT]
    make docs-serve
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import posixpath
import socketserver
import sys
import urllib.parse
from pathlib import Path
from typing import ClassVar

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
OPENAPI_DIR = REPO_ROOT / "docs" / "api" / "openapi"

DEFAULT_HOST = "0.0.0.0"  # noqa: S104 — servidor de documentación local, intencional
DEFAULT_PORT = 8888


class DocsHandler(http.server.SimpleHTTPRequestHandler):
    """Sirve el landing y monta los contratos OpenAPI bajo /openapi/."""

    extensions_map: ClassVar[dict[str, str]] = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".json": "application/json",
        ".html": "text/html",
        ".svg": "image/svg+xml",
    }

    def translate_path(self, path: str) -> str:
        """Mapea la URL a un archivo real, con /openapi/ apuntando fuera del landing."""
        parsed = urllib.parse.urlsplit(path).path
        parsed = urllib.parse.unquote(parsed, errors="surrogatepass")
        parsed = posixpath.normpath(parsed)

        parts = [p for p in parsed.split("/") if p and p not in (".", "..")]

        if parts and parts[0] == "openapi":
            # /openapi/<archivo>.json → docs/api/openapi/<archivo>.json
            base = OPENAPI_DIR
            rest = parts[1:]
        else:
            base = HERE
            rest = parts

        target = base
        for part in rest:
            target = target / part

        # Defensa contra path traversal: el destino debe quedar bajo la base.
        try:
            resolved = target.resolve()
            resolved.relative_to(base.resolve())
        except (ValueError, OSError):
            return str(base)
        return str(resolved)

    def end_headers(self) -> None:
        # Documentación en desarrollo: nunca servir una versión cacheada.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        sys.stderr.write(
            "%s  %s\n" % (self.log_date_time_string(), format % args)  # noqa: UP031
        )


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=os.environ.get("DOCS_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DOCS_PORT", DEFAULT_PORT)))
    args = parser.parse_args(argv)

    if not (HERE / "index.html").exists():
        sys.stderr.write(f"No se encontró index.html en {HERE}\n")
        return 1
    if not OPENAPI_DIR.is_dir():
        sys.stderr.write(f"Aviso: no existe {OPENAPI_DIR}; /openapi/ devolverá 404.\n")

    handler = functools.partial(DocsHandler, directory=str(HERE))

    with Server((args.host, args.port), handler) as httpd:
        shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host  # noqa: S104
        sys.stderr.write(
            f"Documentación de ORDO en http://{shown}:{args.port}/\n"
            f"  landing   → {HERE}\n"
            f"  /openapi/ → {OPENAPI_DIR}\n"
            "Ctrl-C para detener.\n"
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("\nDetenido.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
