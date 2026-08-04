param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^https?://')]
  [string] $TargetUrl,

  [string] $OutputDir = "security-reports",

  [string] $DockerImage = "ghcr.io/zaproxy/zaproxy:stable",

  [switch] $FailOnWarn
)

$ErrorActionPreference = "Stop"

if ($TargetUrl -notmatch '^https://') {
  if ($TargetUrl -notmatch '^http://(localhost|127\.0\.0\.1|\[::1\])(:|/|$)') {
    throw "Use an HTTPS staging URL. HTTP is allowed only for localhost checks."
  }
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
  throw "Docker is required to run the OWASP ZAP baseline scan."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$resolvedOutput = (Resolve-Path $OutputDir).Path
$mount = "${resolvedOutput}:/zap/wrk:rw"

$zapArgs = @(
  "run",
  "--rm",
  "-t",
  "-v",
  $mount,
  $DockerImage,
  "zap-baseline.py",
  "-t",
  $TargetUrl,
  "-r",
  "zap-baseline.html",
  "-J",
  "zap-baseline.json",
  "-w",
  "zap-baseline.md"
)

if (-not $FailOnWarn) {
  $zapArgs += "-I"
}

& docker @zapArgs
exit $LASTEXITCODE
