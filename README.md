# CAO 三 Agent 專案範本：Claude Code＋Codex＋Antigravity

這是一個可以直接下載後在本機試跑的 **CLI Agent Orchestrator（CAO）** 專案範本。它把三個原生 coding CLI 放在同一個受控的專案流程中：Claude Code 負責主要分析、規劃與編寫；Codex 負責 code review、測試與除錯意見；Antigravity CLI 負責 UI／資料查詢與前端輔助。

本範本不是把三個模型的聊天訂閱合併成一個 API，也不是讓三個 Agent 同時任意修改同一個檔案。CAO 會管理三個獨立的原生 CLI session，Agent 之間透過 handoff、assign、MCP、檔案 artifact、review report 與 Git diff 傳遞結果。這種設計比較容易觀察、停止、除錯與恢復。

## 你會得到什麼

| 元件 | 用途 |
|---|---|
| `AGENTS.md` | 三個 harness 共用的 canonical 專案規則與交接協定 |
| `CLAUDE.md` | Claude Code 的薄層入口，匯入 `AGENTS.md` 並補充 Claude lead 規則 |
| `GEMINI.md` | 舊 Gemini 相容工具的相容性說明；目前不是真正的規則來源 |
| `.cao/profiles/` | Claude lead、Codex reviewer、Antigravity UI／data worker 的 CAO profiles |
| `.cao/skills/` | project-handoff、code-review、ui-data 三個可重用 skill |
| `.cao/workflows/three_agent_demo.py` | 可重複執行的四階段示範：Claude → Antigravity → Codex → Claude |
| `scripts/` | 安裝、設定、啟動 CAO server、啟動 Claude lead、執行 demo 與驗證 |
| `docs/` | 權限、資料庫 MCP、疑難排解與工作流說明 |

## 先說清楚：目前版本的可行性

CAO 官方目前支援 Claude Code、Codex CLI 與 Antigravity CLI provider。Claude Code 與 Codex 通常可以在 CAO 的 tmux session 中運作；Antigravity 是需要本機完成 Google sign-in 與一次性 onboarding 的 interactive TUI，因此第一次使用前必須先直接啟動 `agy` 完成初始化。Antigravity 的工具限制主要是 soft enforcement，不能把 prompt 當成絕對安全邊界。

因此，本範本預設採用下列權限原則：Claude lead 可以編寫專案；Codex reviewer 可以讀檔、執行檢查命令並回傳 review，但不應修改主工作樹；Antigravity 可以產生 UI／資料 artifact，但資料庫應使用 read-only MCP credential，且建議在獨立 branch 或 worktree 操作。最後是否合併任何修改，由 Claude 與你人工確認。

## 系統需求

請準備一台 Linux 或 macOS 開發機，並安裝 Python 3.10 以上、tmux 3.3 以上、`uv`、Git，以及三個原生 CLI。CAO 官方目前建議以 uv tool 安裝 GitHub `main` 版本；若你偏好穩定版本，也可以改裝 PyPI release。[1]

| 工具 | 用途 | 驗證命令 |
|---|---|---|
| CAO | session、profile、workflow 與 Agent 協調 | `cao --help` |
| Claude Code | lead、架構、主要編寫與最後整合 | `claude --version` |
| Codex CLI | review、測試、bug reproduction 與除錯建議 | `codex --version` |
| Antigravity CLI | UI、資料 schema、API／build log 查詢與 prototype | `agy --version` |
| tmux | 讓三個原生 CLI 在獨立 terminal session 持續運行 | `tmux -V` |
| uv | 安裝 CAO 與啟動 CAO MCP server | `uv --version` |

你必須分別登入三個 provider。CAO 會使用原生 CLI 的認證狀態；這不代表 ChatGPT Plus 或 Claude Pro 自動變成通用 API 額度。若原生 CLI 要求登入，請直接依該 CLI 的官方流程完成登入。

## 最短試跑流程

### 1. 下載並進入專案

```bash
git clone <你的下載網址> cao-claude-codex-antigravity
cd cao-claude-codex-antigravity
```

如果你使用本次提供的 zip 檔，解壓縮後直接進入資料夾即可。整個範本不包含任何 API key、OAuth token、資料庫密碼或 `.env`。

### 2. 安裝 CAO、註冊 profiles 與 project skills

```bash
./scripts/bootstrap.sh
```

這個腳本會檢查基礎依賴、必要時用 uv 安裝 CAO、註冊三個 CAO profiles，並把本專案的 `.cao/skills` 加到 CAO 的 `skills.extra_dirs`。它不會建立或讀取任何 secret file，也不會使用 `--yolo`。

### 3. 分別完成 provider 登入

```bash
claude
codex
agy
```

每個命令只需要在該機器上完成一次登入／初始化即可。對 Antigravity，請確認可以看到正常的互動提示，並執行：

```bash
agy models
```

如果 `agy` 尚未完成 onboarding，CAO 啟動的 worker 可能停在 onboarding 或 approval prompt；這是正常的 provider 限制，不是 workflow 本身的錯誤。

### 4. 啟動 CAO server

在第一個終端機執行：

```bash
./scripts/start-server.sh
```

這會在本機 loopback 的預設 `127.0.0.1:9889` 啟動 CAO server，並將 server log 放在 `.cao/logs/`。CAO 的 server 是 command-execution surface，請不要把它綁到公網，也不要把 9889 port 暴露到 Internet。[2]

### 5. 先用互動方式啟動 Claude lead

在第二個終端機執行：

```bash
./scripts/launch-lead.sh "請先閱讀 AGENTS.md 與 docs/demo-task.md，分析任務，然後使用 CAO handoff 或 assign 呼叫 Codex reviewer 與 Antigravity UI/data worker。所有結果請寫成 artifact 或 review report，再由你完成最後整合。"
```

你可以在 CAO Web UI `http://127.0.0.1:9889` 或 tmux session 中觀察各個 Agent。這個方式最適合第一次確認三個工具是否能正常啟動與互傳訊息。

### 6. 執行可重複的 workflow demo

確認三個 provider 都已登入並且 CAO server 正在運作後，在第三個終端機執行：

```bash
./scripts/run-demo.sh
```

此 demo 會依序執行四個 step：

| Step | Agent | 結果 |
|---|---|---|
| 1 | `lead_claude` | 讀取任務並實作一個小型範例功能 |
| 2 | `agy_ui_data` | 產生 UI／資料查詢備忘錄與 prototype 建議 |
| 3 | `codex_reviewer` | 讀取最新程式碼、執行檢查並輸出 review |
| 4 | `lead_claude` | 讀 review 與 UI artifact，修正並驗證 |

執行過程的 workflow run ID 會被保留。你可以使用以下命令查看狀態與結果：

```bash
cao workflow status cao-three-agent-demo --json
cao workflow result cao-three-agent-demo --json
```

如果同一個 run ID 已經存在，請使用新的 ID：

```bash
CAO_RUN_ID=three-agent-demo-2 ./scripts/run-demo.sh
```

## 專案規則與檔案交接

`AGENTS.md` 是本範本的唯一 canonical 規則來源。Claude Code 的 `CLAUDE.md` 會匯入它；`GEMINI.md` 只保留給舊版 Gemini 相容工具作為提示，不應分別維護第二套規則。現行 Antigravity／Codex 相容流程以 `AGENTS.md` 為主。

Agent 之間不應傳遞隱藏的思考過程，而應傳遞可以檢查的工程產物，例如 `artifacts/spec.md`、`artifacts/review.md`、`artifacts/ui-notes.md`、`artifacts/schema.md`、測試輸出與 commit hash。這能降低 context fragmentation，也讓你在任何一個 step 失敗時可以人工接手。

## 資料查詢 MCP

本範本沒有硬編碼任何資料庫 provider，因為每個人的資料庫與 credentials 都不同。Antigravity 的 CAO provider 會把 profile 中的 MCP 設定合併到 `~/.gemini/config/mcp_config.json`；你可以依照 `docs/antigravity-mcp.md` 增加 read-only database MCP、GitHub、Notion 或 build-log MCP。

請先用 read-only database role，再把 MCP server 名稱加到 `.cao/profiles/agy_ui_data.md` 的 `mcpServers`。不要把密碼直接寫進 profile；使用環境變數、作業系統 keychain 或該 MCP server 的官方 secret mechanism。

## 常用命令

```bash
# 查看目前 profiles
curl -sf http://127.0.0.1:9889/agents/profiles | python3 -m json.tool

# 查看 session
cao session list
cao session status cao-claude-codex-agy --workers

# 直接傳訊息給 lead session
cao session send cao-claude-codex-agy "請整理目前 Codex review 與 Antigravity UI artifact。"

# 關閉範本的 session
cao shutdown --session cao-claude-codex-agy

# 關閉全部 CAO session；請確認沒有其他專案正在使用 CAO
cao shutdown --all
```

## 安全與現實限制

CAO 的 `reviewer` 與部分 provider 權限是 provider-dependent。Claude Code 具有較完整的 native tool restriction；Codex 與 Antigravity 的 restriction 在 CAO 文件中屬於 soft enforcement，因此不能用它作為 production security boundary。[3] 第一版請只在測試專案使用，讓 Antigravity 使用 read-only data credentials，不要讓任何 worker 直接接觸 production secrets。

CAO 的 workflow step output 會被保留在 journal；不要讓 prompt 或 worker output 回傳 API key、password、cookie 或完整 secret。若需要傳遞大型內容，請寫入專案 artifact，只回傳檔案路徑與摘要。這個範本未提供 production deployment，也不會自動替你建立公開 endpoint。

## 官方參考資料

[1]: https://raw.githubusercontent.com/awslabs/cli-agent-orchestrator/main/README.md "CAO 官方 README"
[2]: https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/fleet-coordinator.md "CAO Fleet Coordinator 與安全邊界"
[3]: https://raw.githubusercontent.com/awslabs/cli-agent-orchestrator/main/docs/tool-restrictions.md "CAO Tool Restrictions"
[4]: https://raw.githubusercontent.com/awslabs/cli-agent-orchestrator/main/docs/agent-profile.md "CAO Agent Profile Format"
[5]: https://raw.githubusercontent.com/awslabs/cli-agent-orchestrator/main/docs/workflows.md "CAO Workflows"
