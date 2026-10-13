# Установка транскрибатора на Windows 10/11 без прав администратора.
#   powershell -ExecutionPolicy ByPass -c "irm <домен>/install.ps1 | iex"
# Повторный запуск безопасен.
$ErrorActionPreference = "Stop"

$Source = if ($env:TRANSCRIBER_SOURCE) { $env:TRANSCRIBER_SOURCE } else { "git+https://github.com/sVerentsov/local_asr_and_masking.git" }
$AppName = "Транскрибатор"

function Say($msg) { Write-Host "`n$msg" -ForegroundColor Cyan }

# 1. uv
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "1/5 Устанавливаю uv…"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
} else {
    Say "1/5 uv уже установлен"
}

# 2. Приложение
Say "2/5 Устанавливаю приложение…"
$installed = (uv tool list 2>$null) -match "^transcriber "
if ($installed) {
    uv tool upgrade transcriber
} else {
    uv tool install --python 3.12 "transcriber @ $Source"
}
$BinDir = (uv tool dir --bin).Trim()

# 3. Модели
Say "3/5 Скачиваю модели распознавания (около 300 МБ)…"
& "$BinDir\transcriber.exe" models download
if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать модели" }

# 4. Ярлык в меню «Пуск»
Say "4/5 Создаю ярлык…"
$Programs = [Environment]::GetFolderPath("Programs")
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut("$Programs\$AppName.lnk")
$Link.TargetPath = "$BinDir\transcriber-app.exe"
$Link.WorkingDirectory = $env:USERPROFILE
$Link.Description = "Локальный транскрибатор звонков с маскированием персональных данных"
$Link.Save()

# 5. Запуск
Say "5/5 Запускаю…"
Start-Process "$BinDir\transcriber-app.exe"
Say "Готово. Приложение открывается в браузере: http://127.0.0.1:7860"
