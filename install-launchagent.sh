#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python3"
PLIST_NAME="com.lollapalooza.usag.plist"
TARGET_PLIST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "錯誤：找不到虛擬環境中的 Python ($VENV_PYTHON)"
    exit 1
fi

mkdir -p "${HOME}/Library/Logs/usag"

echo "正在生成設定檔..."
sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__VENV_PYTHON__|${VENV_PYTHON}|g" \
    -e "s|__HOME__|${HOME}|g" \
    "${PROJECT_DIR}/${PLIST_NAME}" > "${TARGET_PLIST}"

echo "正在載入 LaunchAgent..."
launchctl unload "${TARGET_PLIST}" 2>/dev/null || true
launchctl load "${TARGET_PLIST}"

echo "✓ 已安裝，下次登入會自動啟動。手動測試：launchctl start com.lollapalooza.usag"
