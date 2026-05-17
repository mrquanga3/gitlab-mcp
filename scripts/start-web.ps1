<#
.SYNOPSIS
  Start gitlab-mcp HTTP transport + ngrok tunnel, print connector info
  for claude.ai. Auth is OAuth + passphrase (set via MCP_PASSPHRASE).

.DESCRIPTION
  Kills any previous gitlab-mcp / ngrok process, reads MCP_PASSPHRASE
  from .env if present, starts the MCP HTTP server on $Port, opens an
  ngrok tunnel to it, queries the ngrok local API for the public URL,
  and prints everything you need to paste into claude.ai -> Connectors
  -> Add custom connector. claude.ai will redirect you to a passphrase
  form once on connect.

  State (URL, PIDs) is written to .web-mcp-state.json (gitignored) for
  reuse / cleanup.

.PARAMETER Port
  Local port for the MCP HTTP server. Default 8550.

.PARAMETER Insecure
  Pass --insecure-no-auth to gitlab-mcp-server. No login required. Use ONLY
  for a quick test, then stop immediately.

.EXAMPLE
  .\scripts\start-web.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8500,
    [switch]$Insecure
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$statePath = Join-Path $repoRoot ".web-mcp-state.json"
$envPath = Join-Path $repoRoot ".env"

# --- Prerequisite check ---
$missing = @()
foreach ($cmd in @("uv", "ngrok")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { $missing += $cmd }
}
if ($missing) {
    Write-Host "[!] Missing on PATH: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "    Make sure uv and ngrok are installed and available on your PATH." -ForegroundColor Yellow
    exit 1
}

function Stop-OnPort {
    param([int]$LocalPort)
    $conns = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try {
            $proc = Get-Process -Id $c.OwningProcess -ErrorAction Stop
            Write-Host "  Killing $($proc.ProcessName) (PID $($proc.Id)) on port $LocalPort"
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        } catch {}
    }
}

function Read-DotenvValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path $Path)) { return "" }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $eq = $trimmed.IndexOf("=")
        if ($eq -lt 1) { continue }
        $k = $trimmed.Substring(0, $eq).Trim()
        $v = $trimmed.Substring($eq + 1).Trim().Trim('"').Trim("'")
        if ($k -eq $Key) { return $v }
    }
    return ""
}

# 1. Cleanup prior run
Write-Host "[1/5] Killing previous gitlab-mcp-server / ngrok processes..." -ForegroundColor Cyan
if (Test-Path $statePath) {
    try {
        $prev = Get-Content $statePath -Raw | ConvertFrom-Json
        foreach ($oldPid in @($prev.mcp_pid, $prev.ngrok_pid)) {
            if ($oldPid) { Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue }
        }
    } catch {}
}
Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Stop-OnPort -LocalPort $Port
Start-Sleep -Milliseconds 800

# Copy environment variables from .env to system environment for current process
if (Test-Path $envPath) {
    Write-Host "  Loading configurations from .env..." -ForegroundColor Gray
    foreach ($line in Get-Content $envPath) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $eq = $trimmed.IndexOf("=")
        if ($eq -lt 1) { continue }
        $k = $trimmed.Substring(0, $eq).Trim()
        $v = $trimmed.Substring($eq + 1).Trim().Trim('"').Trim("'")
        [System.Environment]::SetEnvironmentVariable($k, $v)
    }
}

# Resolve port: use environment variable MCP_PORT if specified and not explicitly overridden on command line
if (-not $PSBoundParameters.ContainsKey('Port')) {
    $envPort = [System.Environment]::GetEnvironmentVariable("MCP_PORT")
    if ($envPort) {
        $Port = [int]$envPort
        Write-Host "  Using port $Port configured in .env (MCP_PORT)..." -ForegroundColor Gray
    }
}
Write-Host "Resolved port: $Port" -ForegroundColor Cyan

# 2. Resolve passphrase from env / .env
if (-not $Insecure) {
    $resolved = [System.Environment]::GetEnvironmentVariable("MCP_PASSPHRASE")
    if (-not $resolved) {
        Write-Host ""
        Write-Host "[!] MCP_PASSPHRASE not found in environment or .env." -ForegroundColor Red
        Write-Host "    Add a line to .env:    MCP_PASSPHRASE=<any string you'll remember>"
        Write-Host "    Then re-run this script. Or pass -Insecure for a no-auth test."
        exit 1
    }
} else {
    [System.Environment]::SetEnvironmentVariable("MCP_PASSPHRASE", $null)
}

# Ensure GITLAB_URL and GITLAB_TOKEN are set
$gUrl = [System.Environment]::GetEnvironmentVariable("GITLAB_URL")
$gToken = [System.Environment]::GetEnvironmentVariable("GITLAB_TOKEN")
if (-not $gUrl -or -not $gToken) {
    Write-Host ""
    Write-Host "[!] GITLAB_URL and GITLAB_TOKEN are required to start the server." -ForegroundColor Red
    Write-Host "    Please configure them in your .env file." -ForegroundColor Yellow
    exit 1
}

# 3. Start MCP HTTP
Write-Host "[2/5] Starting gitlab-mcp-server --transport http on port $Port..." -ForegroundColor Cyan
$mcpArgs = @("run", "gitlab-mcp-server", "--transport", "http", "--port", "$Port")
if ($Insecure) { $mcpArgs += "--insecure-no-auth" }

$logPath = Join-Path $repoRoot "mcp-server.log"
$errPath = Join-Path $repoRoot "mcp-server-err.log"
Remove-Item $logPath -ErrorAction SilentlyContinue
Remove-Item $errPath -ErrorAction SilentlyContinue

$mcpProc = Start-Process -FilePath "uv" -ArgumentList $mcpArgs `
    -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $logPath -RedirectStandardError $errPath

Write-Host "  Waiting up to 12s for port $Port to start listening..." -ForegroundColor Gray
$listening = $false
for ($i = 0; $i -lt 12; $i++) {
    if ($mcpProc.HasExited) {
        break
    }
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        break
    }
    Start-Sleep -Seconds 1
}

if ($mcpProc.HasExited -or -not $listening) {
    if ($mcpProc.HasExited) {
        Write-Host "[!] gitlab-mcp-server exited immediately (exit $($mcpProc.ExitCode))." -ForegroundColor Red
    } else {
        Write-Host "[!] Port $Port is not listening after 12s. Server may have failed silently." -ForegroundColor Red
        Stop-Process -Id $mcpProc.Id -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $logPath) {
        Write-Host ""
        Write-Host "--- Server Log Output (from mcp-server.log) ---" -ForegroundColor Yellow
        Get-Content $logPath
        Write-Host "------------------------------------------------" -ForegroundColor Yellow
    }
    if (Test-Path $errPath) {
        Write-Host ""
        Write-Host "--- Server Error Output (from mcp-server-err.log) ---" -ForegroundColor Red
        Get-Content $errPath
        Write-Host "----------------------------------------------------" -ForegroundColor Red
    }
    exit 1
}
Write-Host "  MCP listening on http://127.0.0.1:$Port/mcp (PID $($mcpProc.Id))"

# 4. Start ngrok
Write-Host "[3/5] Starting ngrok tunnel..." -ForegroundColor Cyan
$ngrokProc = Start-Process -FilePath "ngrok" -ArgumentList @("http", "$Port", "--log=stdout") `
    -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3

# 5. Read public URL
Write-Host "[4/5] Reading public URL from http://127.0.0.1:4040/api/tunnels..." -ForegroundColor Cyan
$publicUrl = $null
for ($i = 0; $i -lt 12; $i++) {
    try {
        $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2 -ErrorAction Stop
        $https = $tunnels.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($https) { $publicUrl = $https.public_url; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $publicUrl) {
    Write-Host "[!] Could not read ngrok URL. Authenticated? Try: ngrok config add-authtoken <TOKEN>" -ForegroundColor Red
    Stop-Process -Id $mcpProc.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $ngrokProc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
$mcpUrl = "$publicUrl/mcp"

# 6. Save state
$state = [PSCustomObject]@{
    public_url = $mcpUrl
    insecure   = [bool]$Insecure
    mcp_pid    = $mcpProc.Id
    ngrok_pid  = $ngrokProc.Id
    started_at = (Get-Date).ToString("o")
}
$state | ConvertTo-Json | Set-Content -Encoding UTF8 $statePath

# 7. Print
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "[5/5] Ready. In claude.ai -> Settings -> Connectors -> Add custom connector:" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Name:        GitLab"
Write-Host ("  Remote URL:  {0}" -f $mcpUrl) -ForegroundColor White
Write-Host ""
if (-not $Insecure) {
    Write-Host "  Auth:        OAuth + passphrase" -ForegroundColor Green
    Write-Host "  On connect:  claude.ai will pop a browser window."
    Write-Host "               Type your MCP_PASSPHRASE there, click Authorize."
} else {
    Write-Host "  Auth:        NONE (insecure mode)" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "MCP PID:    $($mcpProc.Id)"
Write-Host "ngrok PID:  $($ngrokProc.Id)"
Write-Host ""
Write-Host "To stop:    .\scripts\stop-web.ps1"
Write-Host "State file: $statePath (gitignored)"
Write-Host ""
