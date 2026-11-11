"""Serve web/ for development, with caching off.

    python scripts/devserver.py [--port 8765] [--directory web]

`python -m http.server` sends no cache headers, so a browser caches ES modules aggressively and
keeps running the previous version of a file after it is edited. That is harmless for a deployment
and actively misleading while iterating: a fix lands on disk, the page keeps the old module, and the
bug appears not to be fixed. This sends `Cache-Control: no-store` on everything, so a reload always
runs what is on disk.

Development only. The deployed site is served by Vercel with the cache headers in vercel.json.

Owner: Yash (lane: scripts/, web/).
"""

from __future__ import annotations

import argparse
import contextlib
import socket
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class NoCacheHandler(SimpleHTTPRequestHandler):
    """Static files, never cached, with the few content types the site needs spelled out."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".woff2": "font/woff2",
        ".wasm": "application/wasm",
    }

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # One line per request is noise while watching a 3D view reload a hundred assets; errors
        # still reach stderr through log_error.
        if args and str(args[0]).startswith(("GET /api", "POST")):
            super().log_message(fmt, *args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--directory", default=str(REPO_ROOT / "web"))
    args = ap.parse_args(argv)

    handler = partial(NoCacheHandler, directory=args.directory)
    ThreadingHTTPServer.allow_reuse_address = True
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as e:
        print(f"cannot bind 127.0.0.1:{args.port}: {e}", file=sys.stderr)
        return 1
    host, port = httpd.socket.getsockname()[:2]
    print(f"serving {args.directory} at http://{host}:{port}/  (no-store; development only)")
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()
    httpd.server_close()
    with contextlib.suppress(OSError):
        socket.socket()
    return 0


if __name__ == "__main__":
    sys.exit(main())
