# Security Notes

## Current Scope

The application is a public-data map. It does not collect user-submitted records,
run administrative web tools, or expose upload endpoints. A private SQLite test
export can be generated for backend experiments, but it is not served by the map
server.

## Implemented Controls

- Public routing is allowlisted in `server.py`; raw CSVs, cached Excel files,
  source JSON, Python scripts, private files, certificates, and logs are not
  served.
- `server.py` rejects `POST`, `PUT`, `PATCH`, and `DELETE` requests.
- Rate limiting is enabled per client address.
- CORS is same-origin by default and only sends cross-origin headers for origins
  explicitly listed in `MAP_ALLOWED_ORIGINS`.
- Security headers are sent by `server.py`, including CSP, `nosniff`,
  anti-framing, referrer policy, permissions policy, COOP, CORP, and HSTS when
  HTTPS is enabled.
- Browser rendering uses DOM APIs and `textContent` for source data; source
  links are restricted to `http` and `https`.
- `script.py` validates project-local input/output paths, upstream hosts,
  HTTPS URLs, response sizes, JSON payload shape, CSV headers, workbook size,
  and workbook zip contents.
- CSV exports protect text fields against spreadsheet formula injection.
- The SQLite test export is built with parameterized inserts, common-query
  indexes, metadata, and input/output change records.
- `.gitignore` excludes local secrets, certificates, private folders, uploads,
  logs, caches, and local database files.
- `data/map-summary.json` records generation time, public source metadata,
  input hashes, output hashes, and record counts.

## Deployment Requirements

- Serve production traffic over HTTPS through a TLS-terminating proxy or by
  running `server.py` with `--certfile` and `--keyfile`.
- Keep API keys out of browser code and source control. Use environment
  variables or a managed secret store for future private integrations.
- Keep administrative tooling outside this public static server. If an admin API
  is added, require authentication, authorization, audit logging, CSRF protection
  for browser sessions, and a separate rate-limit policy.
- Keep the SQLite test database in `private/` or another non-public path. Open
  it read-only for API experiments, keep file permissions restrictive, and never
  expose it through static routing.
- If a production database is added, use least-privilege accounts, read-only
  accounts for map reads, parameterized queries, encrypted connections, and
  migrations with change review.
- If uploads are added, keep them on a separate endpoint and storage path;
  enforce authentication, extension allowlists, MIME sniffing, size limits,
  malware scanning, randomized filenames, and no direct execution.
- Review dependency versions quarterly and after security advisories.
