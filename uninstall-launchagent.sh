#!/bin/bash
set -euo pipefail

PLIST_NAME="com.lollapalooza.usag.plist"
TARGET_PLIST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"

echo "正在卸載 LaunchAgent..."
launchctl unload "${TARGET_PLIST}" 2>/dev/null || true

echo "正在移除檔案..."
rm -f "${TARGET_PLIST}"
rm -f "${HOME}/Library/Logs/usag/usag.log"
rm -f "${HOME}/Library/Logs/usag/usag.err.log"
rmdir "${HOME}/Library/Logs/usag" 2>/dev/null || true

echo "✓ 已移除"
