# Visual Pipeline 實作交接與低成本 Model 執行指南

> 用途：在另一台電腦上，以較便宜的 coding model 依序實作 Plan 1–4。
>
> 最後更新：2026-07-30

## 重要原則

1. 嚴格依序執行：

   ```text
   Plan 1 → Plan 1 completion gate
   → Plan 2 → Plan 2 completion gate
   → Plan 3 → Plan 3 completion gate
   → Plan 4 Code completion gate
   → 人工確認
   → Paid private rollout acceptance gate
   ```

2. 一次只實作一個 Task，不要讓 model 一次完成整份 Plan。
3. 每個 Task 都採 RED → GREEN → 全域回歸測試 → 小型 commit。
4. Code completion gate 以前禁止真實 Gemini、Veo、Lyria、YouTube API call。
5. 不得用單 process counter、thread lock 或 scheduler `max_instances=1`
   取代 SQLite lease、CAS 與 durable intent。
6. 不得透明重試無法判定結果的付費 start 或 YouTube final chunk。
7. 不得降低 assertion、刪除失敗測試或把 integration test 改成只測 mock。
8. 未經明確授權，不 push、不建 PR、不部署、不執行付費 smoke test。

## 計畫文件

實作前必須讀取：

1. `docs/superpowers/plans/2026-07-29-gemini-visual-foundation-preflight.md`
2. `docs/superpowers/plans/2026-07-29-gemini-visual-planning-image-review.md`
3. `docs/superpowers/plans/2026-07-29-veo-loop-generation-video-review.md`
4. `docs/superpowers/plans/2026-07-29-visual-pipeline-timeline-rollout.md`

Plan 4 的 D1–D30、跨計畫契約、Code completion gate 是最高優先契約。
若舊 snippet 與 D1–D30 衝突，以 D1–D30 為準。

## A. 在原電腦保存並同步工作

目前四份 plan 文件可能仍是未提交狀態。離開原電腦前先執行：

```bash
cd /Users/danielyi/Downloads/Lyria-Auto-Publisher
git status --short
git diff --check
```

建議建立獨立 branch：

```bash
git switch -c feat/visual-pipeline
```

提交四份計畫與本交接文件：

```bash
git add \
  docs/superpowers/plans/2026-07-29-gemini-visual-foundation-preflight.md \
  docs/superpowers/plans/2026-07-29-gemini-visual-planning-image-review.md \
  docs/superpowers/plans/2026-07-29-veo-loop-generation-video-review.md \
  docs/superpowers/plans/2026-07-29-visual-pipeline-timeline-rollout.md \
  docs/VISUAL_PIPELINE_IMPLEMENTATION_HANDOFF_zh-TW.md
git commit -m "docs: harden visual pipeline execution plans"
git push -u origin feat/visual-pipeline
```

`git push` 會把內容送到 remote；執行前確認 remote 與 branch 名稱正確。

## B. 在新電腦取得專案

若尚未 clone：

```bash
git clone <REPOSITORY_URL>
cd Lyria-Auto-Publisher
git fetch origin
git switch --track origin/feat/visual-pipeline
```

若已經 clone：

```bash
cd <Lyria-Auto-Publisher所在路徑>
git fetch origin
git switch feat/visual-pipeline
git pull --ff-only
```

確認同步結果：

```bash
git status --short
git log -5 --oneline
git diff --check
```

預期工作樹為乾淨狀態，而且能看到
`docs: harden visual pipeline execution plans` commit。

## C. 建立本機環境

先閱讀專案既有 README、AGENTS.md 與 setup 文件，再依專案目前支援版本建立環境。
不要把 secrets commit 到 repository。

基礎檢查：

```bash
python3 --version
git --version
ffmpeg -version
ffprobe -version
```

若專案既有 `.venv` 建立流程適用：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

先取得未修改程式碼的 baseline：

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check src tests
git status --short
```

把實際 pass/fail 數量記錄在第一個 Task 的回報中。不要假設舊電腦的結果必然相同。

## D. Model 建議

若選項中提供 `gpt-5.6-terra`，一般 Task 可使用 medium reasoning。

較便宜 model 適合：

- lint 與既有 baseline 清理
- dataclass、設定 schema、CLI parser
- pure timeline calculations
- deterministic repository methods
- HTML review state 與 snapshot tests
- 文件與 error snapshot tests

下列區域完成後，至少用較強 model 做一次 read-only review：

- migrations `0001`–`0005`
- SQLite lease、CAS、authorization consumption
- paid-start crash windows
- subprocess restart 與雙 process concurrency
- immutable snapshot 與 tamper handling
- YouTube session-first、308 query/resume
- final-byte response 遺失與 `upload_uncertain`
- Plan 4 Code completion gate

## E. 每個新 Session 的固定總指令

將以下內容完整貼給 model：

```text
請依本專案 AGENTS.md 工作。

實作必須以這四份 plan 為準，並遵守 Plan 4 的 D1–D30、跨計畫契約及
completion gates：

1. docs/superpowers/plans/2026-07-29-gemini-visual-foundation-preflight.md
2. docs/superpowers/plans/2026-07-29-gemini-visual-planning-image-review.md
3. docs/superpowers/plans/2026-07-29-veo-loop-generation-video-review.md
4. docs/superpowers/plans/2026-07-29-visual-pipeline-timeline-rollout.md

工作規則：
- 一次只實作我指定的 Task，不提前實作後續 Plan。
- 先讀 Plan 4 的 D1–D30、跨計畫契約，再讀本次 Task 與 completion gate。
- 先檢查 git status/diff，保留既有修改，不覆蓋其他人的工作。
- 先寫 RED tests，再寫最小實作使其 GREEN。
- 禁止呼叫真實 Gemini、Veo、Lyria 或 YouTube API。
- 禁止執行付費 smoke test。
- 不得以 in-memory lock、scheduler max_instances 或單 process counter
  取代 SQLite lease/CAS。
- 不得使用任意 update_job/update_asset/update_video 更新狀態。
- 不得把 raw exception、token、API key、OAuth secret、provider payload 或
  session URI 寫入 log、DB error、report 或測試輸出。
- 不做無關 refactor，不修改本次 Task 範圍外的檔案。
- 測試失敗時找出 root cause；不要降低 assertion 或刪除測試。
- 完成後執行本 Task 指定測試、pytest -q、ruff check src tests、
  git diff --check 與 git status --short。
- 回報修改檔案、狀態不變量、測試結果、殘餘風險及尚未完成事項。
- 未經要求不要 push、不要建立 PR、不要部署。
- 不需要詢問方案選擇；採用 plan 中最推薦且最保守的方案。
```

## F. 第一個工作：Plan 1 Task 0

在固定總指令之後貼：

```text
現在只執行 Plan 1 的 Task 0：建立乾淨工程基線。

先檢查目前 git diff，保留四份 plan 與 handoff 文件。
執行目前 pytest 與 Ruff，確認實際 baseline。
依 Plan 1 Task 0 修正既有 Ruff errors，但不要開始 visual schema、
Gemini adapter、preflight 或任何後續 Task。

完成條件：
1. pytest -q 通過。
2. ruff check src tests 通過。
3. git diff --check 通過。
4. 沒有外部 API call。
5. 沒有修改四份 plan 的決策內容。
6. 顯示 git diff --stat 及所有變更摘要。

完成後停止，不要開始下一個 Task，不要 push。
```

確認修改正確後提交：

```bash
git status --short
git diff --check
git add <本Task實際修改的檔案>
git commit -m "chore: clear existing quality baseline"
```

## G. 後續單一 Task 指令模板

每次只替換 `PLAN` 與 `TASK`：

```text
現在只實作 Plan PLAN 的 Task TASK。

開始前：
1. 讀取 Plan 4 D1–D30 與跨計畫契約。
2. 讀取 Plan PLAN Task TASK 的完整內容與該 Plan completion gate。
3. 檢查 git status，確認上一個 Task 的測試仍然通過。
4. 列出本 Task 預計修改的檔案；採用 plan 中最推薦的方案，不需問我選項。

執行要求：
- 先新增會失敗的測試並確認失敗原因正確。
- 再完成最小實作。
- 只使用 fake client，禁止真實 API。
- SQLite transition 必須使用明確狀態、expected state_version 與 CAS。
- 涉及 paid start 時，必須保留 authorization、lease、intent 與
  start_uncertain 契約。
- provider call 必須在 SQLite transaction 外。
- 涉及 crash recovery 時，測試必須使用 subprocess/process termination
  和全新 DB connection，不能只重建 Python object。
- 涉及檔案完整性時，consumer 只能使用 content-addressed immutable snapshot。

完成後執行本 Task 指定測試、pytest -q、ruff check src tests、
git diff --check 與 git status --short，然後停止。
不要提前實作下一個 Task，不要 push。
```

每個 Task 經人工檢查後建立一個小型 commit：

```bash
git diff --check
git status --short
git add <本Task實際修改的檔案>
git commit -m "<符合本Task內容的訊息>"
```

## H. Completion Gate 審查指令

完成一份 Plan 後，開一個新的乾淨 session，貼固定總指令，再貼：

```text
暫時不要修改程式碼。

請深度審查目前實作是否完整通過 Plan PLAN completion gate。
從 Plan 4 D1–D30 與跨計畫契約開始，逐項核對實際 source、tests、
SQLite schema 與 migration。

特別驗證：
- 測試是否真的啟動 subprocess。
- crash 後是否真的重新開啟 SQLite。
- 兩個 process 是否真的競爭同一 paid-start identity。
- provider call 是否位於 DB transaction 外。
- authorization consumption 與 reserved→starting CAS 是否同 transaction。
- starting 且無 operation ID 是否只會變成 start_uncertain。
- poll/download transient failure 是否保留 operation ID。
- tampered snapshot 是否不可重新 approve。
- stale browser POST 是否被 state_version CAS 拒絕。
- 是否存在任意或無條件 state update。
- 是否洩漏 raw exception、secret 或 session URI。
- 是否意外呼叫真實外部 API。

依嚴重度輸出 findings。每項包含：
1. 文件或程式碼路徑與行號。
2. 違反的 D1–D30 決策或跨計畫契約。
3. 具體失敗時序。
4. 缺少或不充分的測試。
5. 最小修正建議。

沒有 finding 才能宣告 gate 通過。先不要修改程式碼。
```

將 `PLAN` 替換為 1、2、3。Plan 4 則改成審查 `Code completion gate`。

## I. Finding 修正指令

若 gate review 有 findings，另開 session：

```text
請只修正以下 gate review findings，不要擴大範圍：

<貼上 findings>

要求：
- 先補上能重現失敗時序的 RED test。
- 再做最小修正。
- 不得放寬 D1–D30、狀態不變量或 assertion。
- 禁止真實外部 API。
- 完成後重跑受影響測試、該 Plan 全部測試、pytest -q、
  ruff check src tests 與 git diff --check。
- 回報每個 finding 對應的 test 與修正位置，然後停止。
```

## J. D25–D30 必查清單

進入 Plan 3、4 時逐項確認：

### D25：in-flight 完整性

- `reserved`、`starting`、`polling`、`normalizing` 不可提前進 review。
- 只有全部 asset `ready` 才能進 review gate。
- stale selection ID 必須清除。

### D26：at-most-once

- paid authorization 與 exact identity 綁定。
- allowance consumption 與 `reserved→starting` CAS 在同一 transaction。
- provider call 在 transaction commit 後。
- crash 後沒有 operation ID 時進 `start_uncertain`。
- 普通 resume 不得重送；只能以新 allowance 執行 regenerate。

### D27：tamper handling

- approve 前重新 hash、decode、probe。
- 使用固定 descriptor 或等效機制避免 check/use path race。
- 通過後建立 content-addressed immutable snapshot。
- mismatch 進不可逆 `failed_tampered`。

### D28：provider identity

- Gemini Developer API-key adapter 是唯一 provider path。
- 保存 effective provider、model、SDK、credential fingerprint 與 billing project。
- 不接受任意 endpoint override。

### D29：timeline

- 每個 30 分鐘邊界使用 `[B−1,B+1]`。
- xfade offset 為 `B−1`。
- 真實縮短 render 使用 A→B→C→D→A，包含 D→A 邊界。
- 驗證 PTS、audio stream、每個 boundary 與總長誤差 ≤ 1 frame。

### D30：YouTube resumable upload

- 先檢查 `longUploadsStatus=allowed`。
- session URI、video snapshot SHA、metadata SHA、thumbnail SHA
  必須在第一個 byte 前 commit。
- 每個 PUT 前先保存 chunk intent。
- 308 status query 決定 server-confirmed offset。
- final byte 已嘗試但 response 遺失時必須 `upload_uncertain`。
- final-attempted session 的 404/410 不得自動建立新影片。
- crash tests 必須使用 subprocess termination 與 DB reopen。
- 兩個 process 競爭 resume 時只能有一個 uploader owner。

## K. 每日結束前的同步步驟

確認沒有未預期檔案：

```bash
git status --short
git diff --check
git log -5 --oneline
```

只提交當日已完成且測試通過的 Task：

```bash
git add <已確認檔案>
git commit -m "<本次Task訊息>"
git push
```

如果 Task 尚未完成，不要用模糊 commit 假裝 gate 已通過。可把進度記錄在新的
handoff note，或使用專案既有 context-save 流程。

## L. Code Completion 與付費 Rollout 的界線

Plan 4 Code completion gate 必須全部以 deterministic fake/local tests 通過，
且不得需要付費 API。

只有 Code completion gate 通過並經人工確認後，才能進行
Paid private rollout acceptance gate。Paid rollout 必須：

- 使用明確且精確的預算與次數上限。
- 僅上傳 private/unlisted 測試內容。
- 每個付費 stage 由操作者當次授權。
- 監控 DB audit、provider operation、YouTube attempt 與 sanitized logs。
- 任一 `start_uncertain`、`upload_uncertain` 或 integrity mismatch 立即停止，
  不得自動重試。

## M. 最終完成標準

只有以下條件全部成立，才能宣告實作完成：

1. Plan 1、2、3 completion gates 全部通過。
2. Plan 4 Code completion gate 全部通過。
3. migrations `0001`–`0005` 可由舊 DB 升級、重跑及 rollback。
4. `pytest -q` 通過。
5. `ruff check src tests` 通過。
6. `git diff --check` 通過。
7. subprocess crash、DB reopen、雙 process concurrency tests 通過。
8. 沒有真實 API 被 deterministic test 誤呼叫。
9. 沒有 raw secret、session URI 或 provider payload 洩漏。
10. Paid rollout 尚未執行時，必須明確標記為「待人工授權」，不能視為缺陷或
    自動補跑。
