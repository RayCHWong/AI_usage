# usage

繁體中文 · [English](README.en.md)

[![CI](https://github.com/aqua5230/usage/actions/workflows/check.yml/badge.svg)](https://github.com/aqua5230/usage/actions/workflows/check.yml)
[![Latest Release](https://img.shields.io/github/v/release/aqua5230/usage)](https://github.com/aqua5230/usage/releases/latest)
[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos/)
[![License](https://img.shields.io/github/license/aqua5230/usage)](LICENSE)

`usage` 是一個為 Claude Code 使用者設計的 macOS 工具，能將 5 小時 / 7 天用量顯示在右上角 menu bar 或終端機中，並支援開機自動啟動。

<p align="center">
  <img src="docs/popover.png" alt="usage popover 展開時的樣子" width="320">
</p>

## 資料來源

usage **不打 Anthropic API**、也不讀 Keychain，避免「自己每分鐘 ping 一次也算用量」的觀察者效應。

做法：安裝一個 Claude Code statusLine hook，Claude Code 主進程每次刷新狀態列時會把含 `rate_limits.five_hour.used_percentage` 等欄位的 JSON 餵給 hook，hook 把資料落地到 `~/.claude/usag-status.json`，usage 主程式反向讀這份檔。數字跟 Claude Code 自己看到的完全一致。

```mermaid
flowchart LR
    A[Claude Code 主進程] -->|每次刷新 statusLine<br/>餵 JSON 到 stdin| B[usag-statusline.py<br/>hook script]
    B -->|寫入| C[(~/.claude/<br/>usag-status.json)]
    D[usage menu bar / TUI] -->|讀取| C
    D -->|顯示| E[macOS menu bar]
    F((Anthropic API)) -.x.- D
    style F stroke:#c0392b,stroke-dasharray:5 5
```

讀檔優先順序：

1. `~/.claude/usag-status.json` — usage 自己 hook 寫的
2. `~/.claude/tt-status.json` — fallback，如果使用者也裝了 [token-tracker](https://github.com/stormzhang/token-tracker) 就直接共用

## 需求

- macOS
- Python 3.13
- 已安裝並登入 Claude Code
- 建議使用 GitHub noreply email 作為 commit identity，避免私人 email 外洩：`git config user.email "ID+username@users.noreply.github.com"`

## 下載

```bash
git clone https://github.com/aqua5230/usage.git
cd usage
```

不熟 git 也可以到 [GitHub 專案頁](https://github.com/aqua5230/usage) 點右上角綠色 **Code → Download ZIP**，解壓後 `cd` 進資料夾。

## 建立環境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 首次安裝

跑一次 setup，把 statusLine hook 寫進 Claude Code 設定，然後**重開一次 Claude Code**讓它重新讀 `~/.claude/settings.json` 並刷新一次 statusLine（資料才會落地到磁碟）：

```bash
source .venv/bin/activate
python3 main.py --setup
# 重開 Claude Code 一次
```

setup 會：

- 把 `usag_statusline.py` 複製到 `~/.claude/usag-statusline.py`
- 在 `~/.claude/settings.json` 設定 `statusLine` 指向這個 hook
- 若 settings 內已有自訂 statusLine，會備份到 `settings.usag.previousStatusLine`

要卸載：

```bash
python3 main.py --unsetup
```

unsetup 會還原原本 statusLine、刪 hook 跟 `~/.claude/usag-status.json`。

## 使用模式

### Menu bar 模式（預設）

啟動後會常駐在 macOS 右上角選單列，顯示當前 5 小時用量百分比。

```bash
source .venv/bin/activate
python3 main.py
```

- **顯示格式**：`🐾 37%`；若同時偵測到 Codex 用量則加上 `· 📜 10%` 之類後綴：

  <img src="docs/menubar.png" alt="menu bar 上方顯示樣式" width="240">

- **下拉選單**：可查看 5 小時與週用量細節、重置時間、目前速率與同步狀態。
- **權限提醒**：首次啟動時，macOS 可能會詢問是否允許在背景執行。

### 終端機 TUI 模式

保留原有的 Rich Live 介面，帶有像素風 Clawd 動畫。

```bash
source .venv/bin/activate
python3 main.py --tui
```

## 開機自動啟動

使用 LaunchAgent 設定讓應用程式隨登入自動啟動：

1. **安裝**：
   ```bash
   ./install-launchagent.sh
   ```
2. **手動測試啟動**：
   ```bash
   launchctl start com.lollapalooza.usag
   ```
3. **查看 Log**：
   - 標準輸出：`~/Library/Logs/usag/usag.log`
   - 錯誤輸出：`~/Library/Logs/usag/usag.err.log`
4. **移除**：
   ```bash
   ./uninstall-launchagent.sh
   ```

## 預覽模式

沒裝 hook、或想看 UI 長相時，可用假資料預覽：

```bash
# Menu bar 預覽
python3 main.py --mock

# TUI 預覽
python3 main.py --tui --mock
```

## 可選參數

- `--setup` / `--unsetup`：安裝 / 卸載 Claude Code statusLine hook。
- `--tui`：強制使用終端機 TUI 模式。
- `--interval N`：自訂 UI 重讀狀態檔的秒數，最小值 30，預設 60。
- `--mock`：使用假資料，不讀任何狀態檔。
- `--force-group {0,1,2,3}`：強制指定速率組（僅 TUI 模式有效）。

## 除錯

若要查看內部 OSError 等警告訊息，啟動時加環境變數：

```bash
USAG_DEBUG=1 python3 main.py
```

## 行為說明

- usage 只讀 `~/.claude/usag-status.json` 或 `~/.claude/tt-status.json`，不打網路、不讀 Keychain。
- Claude Code 沒在跑時，狀態檔不會更新，但因為實際用量也不會變（除非 reset 時間到），所以顯示值仍然有效；reset 時間過了會自動歸零。
- 若狀態檔超過 6 小時沒更新，會在狀態訊息標示「狀態檔已 N 分鐘未更新，數字可能過時」。

## 打包成 .app（可選）

想要雙擊就跑、不開終端機，可以打包成 macOS 原生 App：

```bash
./build_app.sh
```

產物在 `dist/usag.app`。雙擊或 `open dist/usag.app` 即可。

⚠️ 因為沒有 Apple Developer 簽章，**第一次開啟時 macOS Gatekeeper 會擋**。
解法：在 Finder 找到 `dist/usag.app` → 右鍵 → 開啟 → 確認開啟。之後就能直接雙擊。

每次發 GitHub Release（push tag `v*` 時），CI 會自動 build 並把 `usag.app.zip` 附加到 Release 頁面，使用者可以直接從 Release 下載。

## 自我檢查指令

```bash
source .venv/bin/activate
ruff check .
mypy .
pytest -v
python3 main.py --tui --mock
```
