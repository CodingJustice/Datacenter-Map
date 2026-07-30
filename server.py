"""
Serve the Leaflet map with restrictive static-file routing and security headers.

The server exposes only the browser assets and generated JSON/GeoJSON files.
It does not accept uploads or user-submitted records.
"""

from __future__ import annotations

import argparse
import json
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
HEALTH_PATH = "/health.json"
PUBLIC_FILES = {
    "/": "us_ai_datacenters_nuclear_map.html",
    "/us_ai_datacenters_nuclear_map.html": "us_ai_datacenters_nuclear_map.html",
    "/accessibility.html": "accessibility.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}
PUBLIC_DATA_SUFFIXES = {".json", ".geojson"}
PUBLIC_VENDOR_SUFFIXES = {".css", ".js", ".png"}
GZIP_ELIGIBLE_SUFFIXES = {".html", ".css", ".js", ".json", ".geojson"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".geojson": "application/geo+json; charset=utf-8",
    ".png": "image/png",
}
HEALTH_CHECK_FILES = (
    "data/map-summary.json",
    "data/us-reference-map.geojson",
    "data/data-center-hubs.geojson",
    "data/data-centers.geojson",
    "data/nuclear-plants.geojson",
    "data/water-sources.geojson",
    "vendor/leaflet/leaflet.js",
    "vendor/leaflet/leaflet.css",
    "vendor/leaflet.markercluster/leaflet.markercluster.js",
    "vendor/leaflet.heat/leaflet-heat.js",
    "app.js",
    "styles.css",
)

BASE_CSP = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "script-src 'self'; "
    "script-src-attr 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://*.basemaps.cartocdn.com "
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
        if self.is_health_request():
            self.send_health(include_body=True)
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
        if self.is_health_request():
            self.send_health(include_body=False)
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

    def is_health_request(self) -> bool:
        return urlsplit(self.path).path == HEALTH_PATH

    def send_health(self, include_body: bool) -> None:
        payload = build_health_payload()
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(HTTPStatus.OK if payload["ok"] else HTTPStatus.SERVICE_UNAVAILABLE)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if include_body:
            self.wfile.write(body)

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
        response_path, is_gzip = self.encoded_response_path(path)

        try:
            handle = response_path.open("rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None

        stat = response_path.stat()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type_for(path))
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        if is_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_cache_headers(path)
        self.end_headers()
        return handle

    def encoded_response_path(self, path: Path) -> tuple[Path, bool]:
        accept_encoding = self.headers.get("Accept-Encoding", "").lower()
        gzip_path = Path(f"{path}.gz")
        if (
            "gzip" in accept_encoding
            and path.suffix.lower() in GZIP_ELIGIBLE_SUFFIXES
            and gzip_path.is_file()
        ):
            return gzip_path, True
        return path, False

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
        elif request_path.startswith("/vendor/"):
            parts = PurePosixPath(request_path.lstrip("/")).parts
            if len(parts) < 2 or parts[0] != "vendor" or any(part in {"", ".", ".."} for part in parts):
                return None
            if Path(parts[-1]).suffix.lower() not in PUBLIC_VENDOR_SUFFIXES:
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
        try:
            relative = path.resolve().relative_to(ROOT)
        except ValueError:
            relative = Path()
        if relative.parts and relative.parts[0] == "data":
            self.send_header("Cache-Control", "public, max-age=3600, must-revalidate")
        elif relative.parts and relative.parts[0] == "vendor":
            self.send_header("Cache-Control", "public, max-age=604800, immutable")
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
        self.send_header("Origin-Agent-Cluster", "?1")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header("X-Download-Options", "noopen")

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


def build_health_payload() -> dict[str, object]:
    files: list[dict[str, object]] = []
    for relative_path in HEALTH_CHECK_FILES:
        path = (ROOT / relative_path).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            exists = False
            size = 0
        else:
            exists = path.is_file()
            size = path.stat().st_size if exists else 0
        files.append({
            "path": relative_path,
            "exists": exists,
            "bytes": size,
        })

    summary_path = ROOT / "data" / "map-summary.json"
    generated_at = ""
    record_counts: dict[str, object] = {}
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        generated_at = str(summary.get("generatedAt", ""))
        counts = summary.get("recordCounts", {})
        if isinstance(counts, dict):
            record_counts = counts
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "ok": all(item["exists"] and item["bytes"] for item in files),
        "generatedAt": generated_at,
        "recordCounts": record_counts,
        "files": files,
    }


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


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
