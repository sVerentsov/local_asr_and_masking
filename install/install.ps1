# Установка транскрибатора на Windows 10/11 без прав администратора.
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/sVerentsov/local_asr_and_masking/main/install/install.ps1 | iex"
# Повторный запуск безопасен.

# В Windows PowerShell 5.1 режим Stop превращает любой вывод uv в stderr (прогресс, предупреждения)
# в ошибку, поэтому успех внешних команд проверяем по коду возврата.
$ErrorActionPreference = "Continue"

$Source = if ($env:TRANSCRIBER_SOURCE) { $env:TRANSCRIBER_SOURCE } else { "git+https://github.com/sVerentsov/local_asr_and_masking.git" }
$AppName = "Транскрибатор"

function Say($msg) { Write-Host "`n$msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nОшибка: $msg" -ForegroundColor Red; exit 1 }

# 1. uv
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "1/5 Устанавливаю uv…"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv не установился" }
} else {
    Say "1/5 uv уже установлен"
}

# 2. Приложение
Say "2/5 Устанавливаю приложение…"
$tools = (cmd /c "uv tool list 2>nul") -join "`n"
if ($tools -match "(?m)^transcriber ") {
    uv tool upgrade transcriber
} else {
    uv tool install --python 3.12 "transcriber @ $Source"
}
if ($LASTEXITCODE -ne 0) { Fail "не удалось установить приложение" }
$BinDir = ((cmd /c "uv tool dir --bin 2>nul") | Select-Object -First 1).Trim()
if (-not (Test-Path "$BinDir\transcriber.exe")) { Fail "команда transcriber не найдена в $BinDir" }

# 3. Модели (проверка SHA-256 внутри команды; уже скачанные пропускаются)
Say "3/5 Скачиваю модели распознавания (около 300 МБ)…"
& "$BinDir\transcriber.exe" models download
if ($LASTEXITCODE -ne 0) { Fail "не удалось скачать модели" }

# 4. Ярлык в меню «Пуск»
Say "4/5 Создаю ярлык в меню «Пуск»…"
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
