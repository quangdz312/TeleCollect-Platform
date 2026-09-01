param(
  [string]$Python = ".\.venv\Scripts\python.exe",
  [switch]$SkipChecks,
  [switch]$UnpackedOnly
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$frontend = Join-Path $repo "frontend"
$electron = Join-Path $repo "local_app\electron"
$package = Join-Path $repo "local_app\package"
$runtime = Join-Path $package "frontend_runtime"
$nodePackage = Join-Path $package "node"
$ffmpegPackage = Join-Path $package "ffmpeg"
$mediaCache = Join-Path $package "cache"
$mediaExtract = Join-Path $package "ffmpeg-extract"
$build = Join-Path $repo "local_app\build"
$dist = Join-Path $repo "local_app\dist"
$release = Join-Path $repo "local_app\release"
$node = (Get-Command node -ErrorAction Stop).Source
$pythonPath = (Resolve-Path (Join-Path $repo $Python)).Path

function Assert-ChildPath([string]$Path) {
  $absolute = [IO.Path]::GetFullPath($Path)
  if (-not $absolute.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to modify a path outside the repository: $absolute"
  }
}

foreach ($path in @($runtime, $nodePackage, $ffmpegPackage, $mediaCache, $mediaExtract, $build, $dist, $release)) { Assert-ChildPath $path }

if (-not $SkipChecks) {
  & $pythonPath -m pytest local_app\tests -q
  if ($LASTEXITCODE -ne 0) { throw "Local app tests failed" }
}

Push-Location $frontend
try {
  $env:NEXT_PUBLIC_API_URL = "http://telecollect-api.invalid"
  $env:NEXT_PUBLIC_API_ORIGIN = "http://telecollect-api.invalid"
  $env:NEXT_PUBLIC_WS_BASE = "ws://telecollect-api.invalid"
  # Đây là nơi thu dữ liệu, nên bản đóng gói phải có màn hình thu. Chạy từ mã
  # nguồn thì `web_shell.py` đặt cờ này; ở đây thiếu nó, Next đọc `.env.local`
  # với giá trị "0" của bản web và cài xong app không có chỗ thu dữ liệu.
  $env:NEXT_PUBLIC_COLLECTION_ENABLED = "1"
  if (-not $SkipChecks) {
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Frontend typecheck failed" }
  }
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed" }
}
finally { Pop-Location }

if (Test-Path -LiteralPath $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
New-Item -ItemType Directory -Force $runtime | Out-Null
Copy-Item (Join-Path $frontend ".next\standalone\*") $runtime -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $runtime ".next") | Out-Null
Copy-Item (Join-Path $frontend ".next\static") (Join-Path $runtime ".next\static") -Recurse -Force
if (Test-Path (Join-Path $frontend "public")) {
  Copy-Item (Join-Path $frontend "public") (Join-Path $runtime "public") -Recurse -Force
}

New-Item -ItemType Directory -Force $nodePackage | Out-Null
Copy-Item -LiteralPath $node (Join-Path $nodePackage "node.exe") -Force

# FFmpeg's official download page points Windows users to this essentials
# build. Cache the archive locally, and ship both tools required by review,
# thumbnails and video export. No machine-wide installation or PATH change is
# performed.
$ffmpegExe = Join-Path $ffmpegPackage "ffmpeg.exe"
$ffprobeExe = Join-Path $ffmpegPackage "ffprobe.exe"
if (-not (Test-Path $ffmpegExe) -or -not (Test-Path $ffprobeExe)) {
  New-Item -ItemType Directory -Force $mediaCache | Out-Null
  $archive = Join-Path $mediaCache "ffmpeg-release-essentials.zip"
  if (-not (Test-Path $archive)) {
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $archive
  }
  if (Test-Path $mediaExtract) { Remove-Item -LiteralPath $mediaExtract -Recurse -Force }
  Expand-Archive -LiteralPath $archive -DestinationPath $mediaExtract
  $downloadedFfmpeg = Get-ChildItem $mediaExtract -Filter ffmpeg.exe -Recurse | Select-Object -First 1
  $downloadedFfprobe = Get-ChildItem $mediaExtract -Filter ffprobe.exe -Recurse | Select-Object -First 1
  if (-not $downloadedFfmpeg -or -not $downloadedFfprobe) { throw "Downloaded FFmpeg archive is incomplete" }
  New-Item -ItemType Directory -Force $ffmpegPackage | Out-Null
  Copy-Item $downloadedFfmpeg.FullName $ffmpegExe -Force
  Copy-Item $downloadedFfprobe.FullName $ffprobeExe -Force
  Remove-Item -LiteralPath $mediaExtract -Recurse -Force
}

Push-Location $repo
try {
  & $pythonPath -m PyInstaller --noconfirm --clean --distpath $dist --workpath $build TeleCollectLocal.spec
  if ($LASTEXITCODE -ne 0) { throw "Backend packaging failed" }
}
finally { Pop-Location }

$backendExe = Join-Path $dist "TeleCollectBackend\TeleCollectBackend.exe"
if (-not (Test-Path -LiteralPath $backendExe)) { throw "Backend executable was not created" }

Push-Location $electron
try {
  if (-not (Test-Path "node_modules\.bin\electron-builder.cmd")) {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "Electron dependencies could not be installed" }
  }
  $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
  if ($UnpackedOnly) { & npm.cmd run pack } else { & npm.cmd run dist }
  if ($LASTEXITCODE -ne 0) { throw "Electron packaging failed" }
}
finally { Pop-Location }

if ($UnpackedOnly) {
  Write-Host "Unpacked app: $release\win-unpacked\TeleCollect Local.exe"
} else {
  $installer = Get-ChildItem $release -Filter "TeleCollectLocalSetup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $installer) { throw "Installer executable was not created" }
  Write-Host "Installer: $($installer.FullName)"
}
