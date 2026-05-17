$ErrorActionPreference = "Continue" # Don't halt if a PID is already gone
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$statePath = Join-Path $repoRoot ".web-mcp-state.json"

Write-Host "Stopping gitlab-mcp web services..." -ForegroundColor Cyan

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
        
        Remove-Item $statePath -Force -ErrorAction SilentlyContinue
        Write-Host "  Cleaned up state file."
    } catch {
        Write-Host "  Failed to read state file: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "  No .web-mcp-state.json found. Killing any running ngrok..." -ForegroundColor Yellow
}

# General cleanup fallback
Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green
