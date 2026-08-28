# 安裝與初始化

## 1. 系統需求

- **Python 3.11 ～ 3.13**
- FFmpeg 與 FFprobe
- 可存取 Gemini API 的 Google 帳號，且**該專案已開通帳單**（見第 3 節）
- 一個 YouTube 頻道
- Google Cloud 專案與 OAuth Desktop App 憑證

## 2. 安裝

macOS 可使用 `brew install python ffmpeg`；Ubuntu/Debian 可使用 `sudo apt install python3 python3-venv ffmpeg`。

```bash
bash scripts/setup.sh
source .venv/bin/activate
```

> ⚠️ **`scripts/setup.sh` 寫死使用 `python3`。** 如果你系統的 `python3` 低於 3.11
> （macOS 內建常是 3.9），安裝會失敗。先確認版本：
>
> ```bash
> python3 --version
> ```
>
> 低於 3.11 就改用明確版本自行建立，不要跑 `setup.sh`：
>
> ```bash
> python3.13 -m venv .venv
> ./.venv/bin/python -m pip install --upgrade pip
> ./.venv/bin/python -m pip install -e ".[dev]"
> ```

## 3. 設定 Gemini API Key

複製 `.env.example` 為 `.env`，填入：

```dotenv
GEMINI_API_KEY=你的金鑰
```

不得把 `.env` commit 到 Git。

> ⚠️ **Lyria 3 沒有免費額度。** 免費層的 Key 呼叫時會回
> `429 ... generate_content_free_tier_input_token_count, limit: 0`。
> 注意 `models.list()` **仍會列出** Lyria 模型，所以不能用「模型看得到」判斷有權限。
> 需到 Google AI Studio 為該專案連結有效的帳單帳戶，升級後即時生效。
>
> 計費為每首固定價（Pro 約 $0.08／首，與長度無關）。

## 4. 設定頻道

```bash
cp config/channels.example.yaml config/channels.yaml
```

預設頻道名稱為 `main`。需要多頻道時，可複製 `main` 區塊，改成其他名稱，且每個頻道使用獨立 token 檔。

## 5. 檢查

```bash
lyria-auto doctor --channel main
```

`youtube token` 在首次授權前顯示 FAIL 是正常的；其餘項目應通過。
