#!/usr/bin/env bash
# Установка транскрибатора на macOS (Apple Silicon) и Linux без прав администратора.
#   curl -fsSL https://raw.githubusercontent.com/sVerentsov/local_asr_and_masking/main/install/install.sh | bash
# Повторный запуск безопасен: выполненные шаги пропускаются.
set -euo pipefail

# Откуда ставить пакет. Можно переопределить: TRANSCRIBER_SOURCE="transcriber==0.1.0" при собственном индексе
# (тогда задайте и UV_INDEX_URL) или путь к локальной копии репозитория.
TRANSCRIBER_SOURCE="${TRANSCRIBER_SOURCE:-git+https://github.com/sVerentsov/local_asr_and_masking.git}"
APP_NAME="Транскрибатор"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }

OS="$(uname -s)"
ARCH="$(uname -m)"
if [ "$OS" = "Darwin" ] && [ "$ARCH" != "arm64" ]; then
  fail "Нужен Mac на Apple Silicon (M1 и новее). Intel-маки пока не поддерживаются."
fi

# 1. uv
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "1/5 Устанавливаю uv…"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=0 sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv не установился"
else
  say "1/5 uv уже установлен"
fi

# 2. Приложение
say "2/5 Устанавливаю приложение…"
if uv tool list 2>/dev/null | grep -q '^transcriber '; then
  uv tool upgrade transcriber || true
else
  uv tool install --python 3.12 "transcriber @ ${TRANSCRIBER_SOURCE}" 2>/dev/null \
    || uv tool install --python 3.12 "${TRANSCRIBER_SOURCE}"
fi
BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
TRANSCRIBER="$BIN_DIR/transcriber"
[ -x "$TRANSCRIBER" ] || fail "команда transcriber не найдена в $BIN_DIR"

# 3. Модели (проверка SHA-256 внутри команды; уже скачанные пропускаются)
say "3/5 Скачиваю модели распознавания (около 300 МБ)…"
"$TRANSCRIBER" models download

# 4. Запускалка
say "4/5 Создаю значок приложения…"
if [ "$OS" = "Darwin" ]; then
  if [ -w /Applications ]; then APPS=/Applications; else APPS="$HOME/Applications"; mkdir -p "$APPS"; fi
  APP="$APPS/$APP_NAME.app"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>local.transcriber.app</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
  # Сервер уходит в фон, запускалка сразу завершается; повторный клик только открывает вкладку.
  cat > "$APP/Contents/MacOS/launch" <<LAUNCH
#!/bin/bash
export PATH="$BIN_DIR:\$PATH"
nohup "$BIN_DIR/transcriber-app" >/dev/null 2>&1 &
LAUNCH
  chmod +x "$APP/Contents/MacOS/launch"
  # Значок собран на этой машине, карантинного атрибута у него нет — Gatekeeper не спрашивает.
else
  DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_DIR/transcriber.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=$APP_NAME
Exec=$BIN_DIR/transcriber-app
Terminal=false
Categories=Office;AudioVideo;
DESK
fi

# 5. Запуск
say "5/5 Запускаю…"
if [ "$OS" = "Darwin" ]; then
  open "$APP"
else
  nohup "$BIN_DIR/transcriber-app" >/dev/null 2>&1 &
fi
say "Готово. Приложение открывается в браузере: http://127.0.0.1:7860"
