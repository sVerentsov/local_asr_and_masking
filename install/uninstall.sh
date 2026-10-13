#!/usr/bin/env bash
# Удаление транскрибатора. Данные дел удаляются только после подтверждения.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
APP_NAME="Транскрибатор"

if [ "$(uname -s)" = "Darwin" ]; then
  DATA="$HOME/Library/Application Support/transcriber"
else
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}/transcriber"
fi

# Остановить сервер, если он запущен.
curl -s -m 2 -X POST -H "X-Requested-With: transcriber" http://127.0.0.1:7860/api/shutdown >/dev/null 2>&1 || true
pkill -f "transcriber-app" 2>/dev/null || true

command -v uv >/dev/null 2>&1 && uv tool uninstall transcriber 2>/dev/null || true
rm -rf "/Applications/$APP_NAME.app" "$HOME/Applications/$APP_NAME.app" \
  "${XDG_DATA_HOME:-$HOME/.local/share}/applications/transcriber.desktop" 2>/dev/null || true

printf 'Удалить также дела, словари соответствий и модели в %s? [y/N] ' "$DATA"
read -r answer </dev/tty || answer=""
case "$answer" in
  y|Y|д|Д|да|Да)
    rm -rf "$DATA"
    echo "Данные удалены."
    ;;
  *)
    echo "Данные оставлены: $DATA"
    ;;
esac
echo "Приложение удалено."
