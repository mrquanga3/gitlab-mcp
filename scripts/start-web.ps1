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

function Stop-NgrokOnPort {
    param([int]$LocalPort)
    # Only kill ngrok agents whose command line targets EXACTLY our port.
    # \b at the end prevents "8500" from matching "85000" or "85001" -- so
    # ngrok agents tunneling other MCPs (different ports) are left alone.
    $pattern = "http\s+$LocalPort\b"
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name = 'ngrok.exe'" -ErrorAction SilentlyContinue
        foreach ($p in $procs) {
            if ($p.CommandLine -match $pattern) {
                Write-Host "  Stopping ngrok (PID $($p.ProcessId)) targeting port $LocalPort..."
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            } else {
                Write-Host "  Leaving ngrok (PID $($p.ProcessId)) alone -- different port." -ForegroundColor Gray
            }
        }
    } catch {}
}

# 1. Cleanup prior run
Write-Host "[1/5] Killing previous gitlab-mcp-server / ngrok processes on port $Port..." -ForegroundColor Cyan
if (Test-Path $statePath) {
    try {
        $prev = Get-Content $statePath -Raw | ConvertFrom-Json
        foreach ($oldPid in @($prev.mcp_pid, $prev.ngrok_pid)) {
            if ($oldPid) { Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue }
        }
    } catch {}
}
Stop-NgrokOnPort -LocalPort $Port
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

# Resolve port: use environment variable MCP_PORT if specified in .env and not explicitly overridden on command line
if (-not $PSBoundParameters.ContainsKey('Port')) {
    $envPort = Read-DotenvValue -Path $envPath -Key "MCP_PORT"
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

# 4. Start ngrok (capture stdout/stderr so we can show why it died if it dies)
# Resolve dedicated authtoken + static domain from .env so this MCP uses its
# own ngrok account, independent of any other agent (e.g. mcp-kanboard) using
# a different token. CLI flags --authtoken/--domain have HIGHEST precedence
# and override ngrok's global config file -- without them ngrok may fall back
# to the global config's token + default static domain (causing ERR_NGROK_334
# "endpoint already online" when the other MCP owns that domain).
Write-Host "[3/5] Starting ngrok tunnel..." -ForegroundColor Cyan
$ngrokToken = [System.Environment]::GetEnvironmentVariable("GITLAB_NGROK_AUTHTOKEN")
if (-not $ngrokToken) { $ngrokToken = [System.Environment]::GetEnvironmentVariable("NGROK_AUTHTOKEN") }
$ngrokDomain = [System.Environment]::GetEnvironmentVariable("GITLAB_NGROK_DOMAIN")

$ngrokArgs = @("http", "$Port", "--log=stdout")
if ($ngrokToken) {
    $ngrokArgs += @("--authtoken", $ngrokToken)
    Write-Host "  Using --authtoken from .env (overrides ngrok global config)." -ForegroundColor Gray
} else {
    Write-Host "  No GITLAB_NGROK_AUTHTOKEN in .env; using ngrok global config token." -ForegroundColor Gray
}
if ($ngrokDomain) {
    $ngrokArgs += @("--url", "https://$ngrokDomain")
    Write-Host "  Using --url https://$ngrokDomain (account static domain)." -ForegroundColor Gray
}

$ngrokLog = Join-Path $repoRoot "ngrok.log"
$ngrokErr = Join-Path $repoRoot "ngrok-err.log"
Remove-Item $ngrokLog -ErrorAction SilentlyContinue
Remove-Item $ngrokErr -ErrorAction SilentlyContinue
$ngrokProc = Start-Process -FilePath "ngrok" -ArgumentList $ngrokArgs `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $ngrokLog -RedirectStandardError $ngrokErr
Start-Sleep -Seconds 2

if ($ngrokProc.HasExited) {
    Write-Host "[!] ngrok exited immediately (exit $($ngrokProc.ExitCode))." -ForegroundColor Red
    if (Test-Path $ngrokLog) {
        Write-Host "--- ngrok stdout ---" -ForegroundColor Yellow
        Get-Content $ngrokLog
    }
    if (Test-Path $ngrokErr) {
        Write-Host "--- ngrok stderr ---" -ForegroundColor Yellow
        Get-Content $ngrokErr
    }
    Write-Host "Common causes:" -ForegroundColor Yellow
    Write-Host "  - Wrong authtoken for the static domain you set" -ForegroundColor Yellow
    Write-Host "  - Another agent on same account already publishing this domain (ERR_NGROK_334)" -ForegroundColor Yellow
    Write-Host "  - Missing authtoken entirely: ngrok config add-authtoken <TOKEN>" -ForegroundColor Yellow
    Stop-Process -Id $mcpProc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

# 5. Resolve public URL
# Fast path: when GITLAB_NGROK_DOMAIN is set, ngrok was launched with
# --url https://<domain>, so the public URL is known up front -- no need to
# probe the local API at all. Slow path (no explicit domain): probe each
# ngrok agent's local API (4040, 4041, ...) and pick the tunnel whose
# config.addr points at OUR $Port. Without this filter, another running
# ngrok agent (e.g. for mcp-kanboard) would have its URL grabbed instead and
# claude.ai would land on the wrong server's OAuth form.
$publicUrl = $null
if ($ngrokDomain) {
    Write-Host "[4/5] Using static domain from .env: https://$ngrokDomain" -ForegroundColor Cyan
    for ($i = 0; $i -lt 8; $i++) {
        if ($ngrokProc.HasExited) {
            Write-Host "[!] ngrok died after launch -- see logs above." -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 1
        try {
            $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 1 -ErrorAction Stop
            if ($tunnels.tunnels | Where-Object { $_.public_url -match $ngrokDomain }) {
                $publicUrl = "https://$ngrokDomain"
                break
            }
        } catch {}
    }
    if (-not $publicUrl -and -not $ngrokProc.HasExited) {
        $publicUrl = "https://$ngrokDomain"
    }
} else {
    Write-Host "[4/5] Reading public URL for port $Port (probing ngrok APIs 4040-4044)..." -ForegroundColor Cyan
    $portPattern = ":$Port(`$|/)"
    # Total budget ~24s: 12 outer attempts x (5 ports x 1s timeout) + 1s sleep
    for ($i = 0; $i -lt 12; $i++) {
        foreach ($apiPort in 4040..4044) {
            try {
                $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:$apiPort/api/tunnels" -TimeoutSec 1 -ErrorAction Stop
                $https = $tunnels.tunnels | Where-Object {
                    $_.proto -eq "https" -and $_.config.addr -match $portPattern
                } | Select-Object -First 1
                if ($https) { $publicUrl = $https.public_url; break }
            } catch {}
        }
        if ($publicUrl) { break }
        if ($ngrokProc.HasExited) {
            Write-Host "[!] ngrok died while we were waiting for its tunnel." -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 1
    }
}
if (-not $publicUrl) {
    Write-Host "[!] Could not find an ngrok tunnel for port $Port." -ForegroundColor Red
    if (Test-Path $ngrokLog) {
        Write-Host "--- ngrok stdout (last 20 lines) ---" -ForegroundColor Yellow
        Get-Content $ngrokLog -Tail 20
    }
    if (Test-Path $ngrokErr) {
        Write-Host "--- ngrok stderr (last 20 lines) ---" -ForegroundColor Yellow
        Get-Content $ngrokErr -Tail 20
    }
    Write-Host "If another ngrok agent is already running (e.g. for mcp-kanboard)," -ForegroundColor Yellow
    Write-Host "the free plan only allows one static domain per account. Set" -ForegroundColor Yellow
    Write-Host "GITLAB_NGROK_AUTHTOKEN + GITLAB_NGROK_DOMAIN in .env to a SECOND" -ForegroundColor Yellow
    Write-Host "ngrok account's credentials." -ForegroundColor Yellow
    Stop-Process -Id $mcpProc.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $ngrokProc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
if ($publicUrl.EndsWith("/")) { $publicUrl = $publicUrl.TrimEnd("/") }
$mcpUrl = "$publicUrl/gitlab-mcp"

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
