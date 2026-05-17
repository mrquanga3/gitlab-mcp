$ErrorActionPreference = "Continue" # Don't halt if a PID is already gone
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$statePath = Join-Path $repoRoot ".web-mcp-state.json"
$envPath = Join-Path $repoRoot ".env"

Write-Host "Stopping gitlab-mcp web services..." -ForegroundColor Cyan

function Stop-OnPort {
    param([int]$LocalPort)
    $conns = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try {
            $proc = Get-Process -Id $c.OwningProcess -ErrorAction Stop
            Write-Host "  Stopping process $($proc.ProcessName) (PID $($proc.Id)) on port $LocalPort..."
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        } catch {}
    }
}

function Stop-NgrokOnPort {
    param([int]$LocalPort)
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name = 'ngrok.exe'" -ErrorAction SilentlyContinue
        foreach ($p in $procs) {
            if ($p.CommandLine -match "http\s+$LocalPort") {
                Write-Host "  Stopping ngrok (PID $($p.ProcessId)) targeting port $LocalPort..."
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {}
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

# 1. Try stopping via recorded state file
$statePort = 0
if (Test-Path $statePath) {
    try {
        $state = Get-Content $statePath -Raw | ConvertFrom-Json
        
        if ($state.mcp_pid) {
            Write-Host "  Stopping gitlab-mcp-server (PID $($state.mcp_pid))..."
            Stop-Process -Id $state.mcp_pid -Force -ErrorAction SilentlyContinue
        }
        if ($state.ngrok_pid) {
            Write-Host "  Stopping ngrok (PID $($state.ngrok_pid))..."
            Stop-Process -Id $state.ngrok_pid -Force -ErrorAction SilentlyContinue
        }
        
        # Try to parse port from the saved public URL (e.g. from state file metadata)
        if ($state.public_url -match ":(\d+)/mcp") {
            $statePort = [int]$Matches[1]
        }
        
        Remove-Item $statePath -Force -ErrorAction SilentlyContinue
        Write-Host "  Cleaned up state file."
    } catch {
        Write-Host "  Failed to read state file: $_" -ForegroundColor Yellow
    }
}

# 2. Target fallback ports to prevent orphan listeners
$envPort = Read-DotenvValue -Path $envPath -Key "MCP_PORT"
$resolvedPort = 8500 # Default
if ($statePort -gt 0) {
    $resolvedPort = $statePort
} elseif ($envPort) {
    $resolvedPort = [int]$envPort
}

Write-Host "  Ensuring no orphan services remain on port $resolvedPort..." -ForegroundColor Gray
Stop-NgrokOnPort -LocalPort $resolvedPort
Stop-OnPort -LocalPort $resolvedPort

Write-Host "Done." -ForegroundColor Green
