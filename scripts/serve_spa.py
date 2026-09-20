#!/usr/bin/env python3
"""Serve the CRA production build on 0.0.0.0 (SPA fallback to index.html)."""
from __future__ import annotations

import argparse
import http.server
import os
from functools import partial


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.translate_path(self.path.split("?", 1)[0].split("#", 1)[0])
        if os.path.isdir(path):
            index = os.path.join(path, "index.html")
            if os.path.isfile(index):
                return super().do_GET()
        if os.path.isfile(path):
            return super().do_GET()
        self.path = "/index.html"
        return super().do_GET()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    handler = partial(SpaHandler, directory=root)
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    print(f"SPA listening on http://{args.host}:{args.port} root={root}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
