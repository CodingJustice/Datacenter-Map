"""
Serve the Leaflet map with restrictive static-file routing and security headers.

The server exposes only the browser assets and generated JSON/GeoJSON files.
It does not accept uploads or user-submitted records.
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = {
    "/": "us_ai_datacenters_nuclear_map.html",
    "/us_ai_datacenters_nuclear_map.html": "us_ai_datacenters_nuclear_map.html",
    "/accessibility.html": "accessibility.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}
PUBLIC_DATA_SUFFIXES = {".json", ".geojson"}

BASE_CSP = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "script-src 'self' https://unpkg.com; "
    "script-src-attr 'none'; "
    "style-src 'self' https://unpkg.com 'unsafe-inline'; "
    "img-src 'self' data: https://unpkg.com https://*.basemaps.cartocdn.com "
    "https://server.arcgisonline.com https://*.arcgisonline.com; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'none'; "
    "manifest-src 'self'"
)


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = max(1, limit)
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            bucket = self._requests[key]
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
        return True, 0


class MapRequestHandler(SimpleHTTPRequestHandler):
    server_version = "DatacenterMap"
    sys_version = ""

    def do_GET(self) -> None:
        if not self.check_rate_limit():
            return
        handle = self.send_head()
        if handle:
            try:
                self.copyfile(handle, self.wfile)
            finally:
                handle.close()

    def do_HEAD(self) -> None:
        if not self.check_rate_limit():
            return
        handle = self.send_head()
        if handle:
            handle.close()

    def do_OPTIONS(self) -> None:
        if not self.check_rate_limit():
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "If-Modified-Since, If-None-Match")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_POST(self) -> None:
        self.method_not_allowed()

    def do_PUT(self) -> None:
        self.method_not_allowed()

    def do_PATCH(self) -> None:
        self.method_not_allowed()

    def do_DELETE(self) -> None:
        self.method_not_allowed()

    def method_not_allowed(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b"Method not allowed.\n")

    def check_rate_limit(self) -> bool:
        allowed, retry_after = self.server.rate_limiter.allow(self.client_address[0])
        if allowed:
            return True
        self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
        self.send_header("Retry-After", str(retry_after))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b"Too many requests.\n")
        return False

    def send_head(self):
        path = self.resolve_public_path()
        if not path:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None

        try:
            handle = path.open("rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None

        stat = path.stat()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.send_cache_headers(path)
        self.end_headers()
        return handle

    def resolve_public_path(self) -> Path | None:
        request_path = urlsplit(self.path).path
        if request_path in PUBLIC_FILES:
            candidate = ROOT / PUBLIC_FILES[request_path]
        elif request_path.startswith("/data/"):
            parts = PurePosixPath(request_path.lstrip("/")).parts
            if len(parts) < 2 or parts[0] != "data" or any(part in {"", ".", ".."} for part in parts):
                return None
            if Path(parts[-1]).suffix.lower() not in PUBLIC_DATA_SUFFIXES:
                return None
            candidate = ROOT.joinpath(*parts)
        else:
            return None

        resolved = candidate.resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            return None
        return resolved if resolved.is_file() else None

    def send_cache_headers(self, path: Path) -> None:
        if path.parent.name == "data":
            self.send_header("Cache-Control", "public, max-age=3600, must-revalidate")
        else:
            self.send_header("Cache-Control", "no-cache")

    def end_headers(self) -> None:
        self.send_security_headers()
        self.send_cors_headers()
        super().end_headers()

    def send_security_headers(self) -> None:
        csp = BASE_CSP
        if getattr(self.server, "use_https", False):
            csp = f"{csp}; upgrade-insecure-requests"
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        phrase = HTTPStatus(code).phrase if code in HTTPStatus._value2member_map_ else "Error"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(f"{code} {phrase}\n".encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {format % args}\n")


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def env_rate_limit(default: int) -> int:
    value = os.environ.get("MAP_RATE_LIMIT_PER_MINUTE", "")
    if not value:
        return default
    try:
        return positive_int(value)
    except argparse.ArgumentTypeError:
        return default


def env_allowed_origins() -> frozenset[str]:
    origins = os.environ.get("MAP_ALLOWED_ORIGINS", "")
    return frozenset(origin.strip() for origin in origins.split(",") if origin.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the generated Leaflet map.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=positive_int, default=8000)
    parser.add_argument("--rate-limit", type=positive_int, default=env_rate_limit(300))
    parser.add_argument("--certfile", type=Path)
    parser.add_argument("--keyfile", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if bool(args.certfile) != bool(args.keyfile):
        raise SystemExit("--certfile and --keyfile must be supplied together.")

    server = ThreadingHTTPServer((args.host, args.port), MapRequestHandler)
    server.rate_limiter = RateLimiter(args.rate_limit)
    server.allowed_origins = env_allowed_origins()
    server.use_https = bool(args.certfile and args.keyfile)

    scheme = "http"
    if server.use_https:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.certfile, args.keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    print(f"Serving map at {scheme}://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
