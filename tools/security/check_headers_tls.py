#!/usr/bin/env python3
"""Check production security headers and basic TLS posture for the static map."""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ACCEPTED_TLS_VERSIONS = {"TLSv1.2", "TLSv1.3"}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def normalized_headers(response) -> dict[str, str]:
    return {key.lower(): value.strip() for key, value in response.headers.items()}


def add(results: list[CheckResult], name: str, ok: bool, detail: str) -> None:
    results.append(CheckResult(name=name, ok=ok, detail=detail))


def has_token(header: str, token: str) -> bool:
    return token.lower() in header.lower()


def check_headers(headers: dict[str, str], final_url: str, allow_http_localhost: bool) -> list[CheckResult]:
    results: list[CheckResult] = []
    parsed = urllib.parse.urlparse(final_url)
    host = (parsed.hostname or "").lower()
    is_local_http = parsed.scheme == "http" and host in LOCAL_HOSTS and allow_http_localhost

    add(results, "final-url-is-https", parsed.scheme == "https" or is_local_http, final_url)

    csp = headers.get("content-security-policy", "")
    add(results, "csp-present", bool(csp), "Content-Security-Policy header")
    for directive in (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "script-src 'self'",
        "script-src-attr 'none'",
        "connect-src 'self'",
    ):
        add(results, f"csp-{directive.split()[0]}", has_token(csp, directive), directive)
    add(results, "csp-no-unpkg", "unpkg.com" not in csp.lower(), "CSP must not allow the former CDN host")

    add(
        results,
        "x-content-type-options",
        headers.get("x-content-type-options", "").lower() == "nosniff",
        headers.get("x-content-type-options", ""),
    )
    add(
        results,
        "x-frame-options",
        headers.get("x-frame-options", "").lower() in {"deny", "sameorigin"} or "frame-ancestors" in csp.lower(),
        headers.get("x-frame-options", ""),
    )
    add(
        results,
        "referrer-policy",
        headers.get("referrer-policy", "").lower()
        in {"no-referrer", "same-origin", "strict-origin", "strict-origin-when-cross-origin"},
        headers.get("referrer-policy", ""),
    )
    add(results, "permissions-policy", bool(headers.get("permissions-policy")), headers.get("permissions-policy", ""))
    add(
        results,
        "cross-origin-opener-policy",
        headers.get("cross-origin-opener-policy", "").lower() == "same-origin",
        headers.get("cross-origin-opener-policy", ""),
    )
    add(
        results,
        "cross-origin-resource-policy",
        headers.get("cross-origin-resource-policy", "").lower() in {"same-origin", "same-site"},
        headers.get("cross-origin-resource-policy", ""),
    )
    add(
        results,
        "x-permitted-cross-domain-policies",
        headers.get("x-permitted-cross-domain-policies", "").lower() == "none",
        headers.get("x-permitted-cross-domain-policies", ""),
    )

    hsts = headers.get("strict-transport-security", "")
    hsts_ok = is_local_http or ("max-age=" in hsts.lower() and "31536000" in hsts)
    add(results, "strict-transport-security", hsts_ok, hsts or "required for HTTPS staging")

    server = headers.get("server", "")
    add(
        results,
        "server-header-minimized",
        not any(token in server.lower() for token in ("python", "simplehttp", "apache/", "nginx/")),
        server or "absent",
    )

    return results


def check_tls(final_url: str, allow_http_localhost: bool) -> list[CheckResult]:
    results: list[CheckResult] = []
    parsed = urllib.parse.urlparse(final_url)
    host = parsed.hostname
    if parsed.scheme != "https":
        skipped = allow_http_localhost and (host or "").lower() in LOCAL_HOSTS
        add(results, "tls-negotiated-version", skipped, "skipped for local HTTP" if skipped else "HTTPS required")
        return results

    if not host:
        add(results, "tls-negotiated-version", False, "missing host")
        return results

    port = parsed.port or 443
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        with socket.create_connection((host, port), timeout=10) as raw_sock:
            with context.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
                version = tls_sock.version() or ""
                add(results, "tls-negotiated-version", version in ACCEPTED_TLS_VERSIONS, version)
                cert = tls_sock.getpeercert()
                add(results, "tls-certificate-validated", bool(cert), "certificate chain and hostname validated")
    except (OSError, ssl.SSLError) as exc:
        add(results, "tls-negotiated-version", False, str(exc))
        add(results, "tls-certificate-validated", False, str(exc))
    return results


def fetch(target_url: str):
    request = urllib.request.Request(
        target_url,
        headers={"User-Agent": "datacenter-map-security-check/1.0"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as exc:
        response = exc
    response.read(2048)
    return response


def serialize(results: Iterable[CheckResult]) -> list[dict[str, object]]:
    return [result.__dict__ for result in results]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate map deployment headers and TLS.")
    parser.add_argument("target_url", help="HTTPS staging URL to test.")
    parser.add_argument(
        "--allow-http-localhost",
        action="store_true",
        help="Allow HTTP only for local development server checks.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args()

    parsed = urllib.parse.urlparse(args.target_url)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit("target_url must start with http:// or https://")
    if parsed.scheme == "http" and not (
        args.allow_http_localhost and (parsed.hostname or "").lower() in LOCAL_HOSTS
    ):
        raise SystemExit("Use an HTTPS staging URL. HTTP is allowed only with --allow-http-localhost.")

    response = fetch(args.target_url)
    final_url = response.geturl()
    headers = normalized_headers(response)

    results = check_headers(headers, final_url, args.allow_http_localhost)
    results.extend(check_tls(final_url, args.allow_http_localhost))
    failures = [result for result in results if not result.ok]

    if args.json:
        print(json.dumps({"target": args.target_url, "finalUrl": final_url, "results": serialize(results)}, indent=2))
    else:
        print(f"Target: {args.target_url}")
        print(f"Final URL: {final_url}")
        for result in results:
            status = "PASS" if result.ok else "FAIL"
            print(f"{status} {result.name}: {result.detail}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
