#!/usr/bin/env pwsh
<#
.SYNOPSIS
    BannerSpec Stage 8 Smoke Test (Windows PowerShell)

.DESCRIPTION
    Docker Compose를 사용해 JDK17 빌드·테스트·API·MongoDB·HTTP 검증을 한 번에 실행.
    smoke 전용 프로젝트 이름으로 격리 — 기존 개발 컨테이너 무영향.

.EXAMPLE
    .\scripts\run-stage8-smoke.ps1
    .\scripts\run-stage8-smoke.ps1 -NoBuild      # 이미지 재사용
    .\scripts\run-stage8-smoke.ps1 -SkipCleanup  # 디버깅용 컨테이너 유지
#>

param(
    [switch]$NoBuild,
    [switch]$SkipCleanup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PROJECT   = "creative-resizer-stage8-smoke"
$COMPOSE_F = Join-Path $PSScriptRoot "..\docker-compose.smoke.yml"
$COMPOSE_F = (Resolve-Path $COMPOSE_F).Path

# Docker CLI 탐색
$DOCKER_PATHS = @(
    "docker",
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
)
$DOCKER = $null
foreach ($p in $DOCKER_PATHS) {
    $cmd = Get-Command $p -ErrorAction SilentlyContinue
    if ($cmd) { $DOCKER = $cmd.Source; break }
    if (Test-Path $p) { $DOCKER = $p; break }
}

if (-not $DOCKER) {
    Write-Error "Docker CLI not found. Install Docker Desktop and ensure it is running."
    exit 1
}
Write-Host "Docker CLI: $DOCKER"

# Docker daemon 확인
$daemonCheck = & $DOCKER info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error @"
Docker daemon is not running.
Start Docker Desktop and wait for it to be ready, then re-run this script.
Error: $($daemonCheck | Select-String 'error|failed' | Select-Object -First 2)
"@
    exit 1
}
Write-Host "Docker daemon: UP"

# compose 명령 확인 (v2: docker compose / v1: docker-compose)
$COMPOSE_CMD = $null
$v2Test = & $DOCKER compose version 2>&1
if ($LASTEXITCODE -eq 0) {
    $COMPOSE_CMD = @($DOCKER, "compose")
} else {
    $dc = Get-Command "docker-compose" -ErrorAction SilentlyContinue
    if ($dc) { $COMPOSE_CMD = @("docker-compose") }
    else {
        Write-Error "Neither 'docker compose' (v2) nor 'docker-compose' (v1) found."
        exit 1
    }
}
Write-Host "Compose: $($COMPOSE_CMD -join ' ')"

# ── 정리 함수 ────────────────────────────────────────────────────────────────
function Invoke-SmokeCompose {
    param([string[]]$Args)
    & $COMPOSE_CMD[0] ($COMPOSE_CMD[1..$COMPOSE_CMD.Count] + @("-p", $PROJECT, "-f", $COMPOSE_F) + $Args)
}

function Cleanup {
    if ($SkipCleanup) {
        Write-Host "`n[INFO] SkipCleanup: containers kept for inspection"
        Write-Host "       docker compose -p $PROJECT -f $COMPOSE_F logs"
        return
    }
    Write-Host "`n[INFO] Cleaning up smoke environment..."
    Invoke-SmokeCompose "down", "-v", "--remove-orphans" 2>&1 | Out-Null
    Write-Host "[INFO] Cleanup done."
}

# ── 1. 기존 smoke 프로젝트 정리 ─────────────────────────────────────────────
Write-Host "`n[1/5] Cleaning up previous smoke run..."
Invoke-SmokeCompose "down", "-v", "--remove-orphans" 2>&1 | Out-Null

# ── 2. 이미지 빌드 (JDK17 + 테스트 포함) ─────────────────────────────────────
if (-not $NoBuild) {
    Write-Host "`n[2/5] Building images (JDK17 compile + test inside Docker)..."
    Invoke-SmokeCompose "build", "--no-cache"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[FAIL] Image build failed — JDK17 compile or test failed"
        Cleanup
        exit 1
    }
    Write-Host "[PASS] Image build succeeded (JDK17 compileJava + test PASS)"
} else {
    Write-Host "`n[2/5] Skipping build (--NoBuild)"
}

# ── 3. Smoke 실행 (MongoDB + RabbitMQ + API + HTTP runner) ───────────────────
Write-Host "`n[3/5] Starting smoke environment..."
Write-Host "      (MongoDB → RabbitMQ → API[JDK17] → smoke runner)"

$smokeExit = 0
try {
    Invoke-SmokeCompose "up", "--abort-on-container-exit", "--exit-code-from", "smoke"
    $smokeExit = $LASTEXITCODE
} catch {
    $smokeExit = 1
}

# ── 4. 로그 수집 ─────────────────────────────────────────────────────────────
Write-Host "`n[4/5] Collecting logs..."

Write-Host "`n--- smoke runner log ---"
Invoke-SmokeCompose "logs", "smoke" 2>&1 | Select-Object -Last 60

if ($smokeExit -ne 0) {
    Write-Host "`n--- api log (last 40 lines) ---"
    Invoke-SmokeCompose "logs", "--tail=40", "api" 2>&1

    Write-Host "`n--- mongo log (last 20 lines) ---"
    Invoke-SmokeCompose "logs", "--tail=20", "mongo" 2>&1

    Write-Host "`n--- rabbitmq log (last 20 lines) ---"
    Invoke-SmokeCompose "logs", "--tail=20", "rabbitmq" 2>&1
}

# ── 5. 정리 ──────────────────────────────────────────────────────────────────
Write-Host "`n[5/5] Cleanup..."
Cleanup

# ── 결과 ─────────────────────────────────────────────────────────────────────
Write-Host ""
if ($smokeExit -eq 0) {
    Write-Host "╔══════════════════════════════════════╗"
    Write-Host "║  BannerSpec Stage 8 Smoke: PASS ✓   ║"
    Write-Host "╚══════════════════════════════════════╝"
} else {
    Write-Host "╔══════════════════════════════════════╗"
    Write-Host "║  BannerSpec Stage 8 Smoke: FAIL ✗   ║"
    Write-Host "╚══════════════════════════════════════╝"
}
exit $smokeExit
