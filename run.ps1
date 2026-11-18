# Abre los tres Chrome con sesiones independientes y deja el stream delante.
# Ejecutar desde PowerShell:  .\run.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) { throw "No encuentro Chrome ni Edge instalado." }

$flags = @(
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
    "--autoplay-policy=no-user-gesture-required"
)

function Start-Browser([string]$name, [int]$port, [string]$url, [string[]]$extra = @()) {
    $profile = Join-Path $root "profiles\$name"
    New-Item -ItemType Directory -Force -Path $profile | Out-Null
    $args = $flags + @("--user-data-dir=$profile", "--remote-debugging-port=$port") + $extra + @($url)
    return Start-Process -FilePath $chrome -ArgumentList $args -PassThru
}

New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null

$stream = Start-Browser "stream" 9222 "https://www.twitch.tv/rubius" @("--start-maximized")
Start-Browser "xbox" 9223 "https://www.xbox.com/es-CO/redeem"

# Sony bloquea el Chrome automatizado en esta maquina; Brave funciona, asi
# que la tienda PlayStation se abre con Brave en el mismo puerto 9224.
$brave = @(
    "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
    "${env:ProgramFiles(x86)}\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($brave) {
    $psProfile = Join-Path $root "profiles\ps-brave"
    New-Item -ItemType Directory -Force -Path $psProfile | Out-Null
    Start-Process -FilePath $brave -ArgumentList ($flags + @(
        "--user-data-dir=$psProfile", "--remote-debugging-port=9224",
        "https://store.playstation.com/es-co/pages/latest"
    )) | Out-Null
} else {
    Start-Browser "ps" 9224 "https://store.playstation.com/es-co/pages/latest"
}

# El stream debe quedar visible y maximizado: es lo que se captura.
Start-Sleep -Seconds 4
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class RgtaWin32 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
}
"@
$stream.Refresh()
if ($stream.MainWindowHandle -ne [IntPtr]::Zero) {
    [RgtaWin32]::ShowWindow($stream.MainWindowHandle, 3) | Out-Null  # SW_MAXIMIZE
    [RgtaWin32]::SetForegroundWindow($stream.MainWindowHandle) | Out-Null
}
Write-Output "Chrome abiertos. Ahora haz login en las dos tiendas (una sola vez) y deja el dialogo de canje abierto."
Write-Output "Luego: python -m rgta doctor"
