# YouTube OAuth 與自動上傳設定

## Google Cloud Console

1. 建立或選擇 Google Cloud 專案。
2. 啟用 YouTube Data API v3。
3. 設定 OAuth consent screen。
4. 建立 OAuth Client ID，Application type 選 Desktop app。
5. 下載 JSON，存成 `secrets/client_secret.json`。

## 第一次授權

```bash
lyria-auto authorize-youtube --channel main
```

瀏覽器會開啟 Google 登入頁。請選擇實際管理目標 YouTube 頻道的帳號。完成後會建立 `secrets/youtube_token_main.json`。

此後程式會使用 refresh token 自動更新短期 access token，不需要每次登入。

## 播放清單

在 YouTube 開啟播放清單，網址中的 `list=` 後方值即為 playlist ID，填入：

```yaml
playlist:
  id: "PLxxxxxxxx"
  title: "Coffeehouse Lofi Jazz"
  create_if_missing: true
```

`id` 留空且 `create_if_missing: true` 時，程式會尋找同名播放清單；找不到就自動建立。

## 排程發布

```yaml
privacy_status: "private"
publish:
  mode: "scheduled"
  delay_hours: 24
  spacing_hours: 24
```

YouTube 的 `publishAt` 只能套用到從未公開且目前為 private 的影片，因此程式在 scheduled 模式會強制使用 private。

## 未驗證 API 專案

部分新建立且未經 YouTube 稽核的 API 專案，上傳內容可能被限制為 private。這是平台端限制，不是程式錯誤。需要公開發布時，必須依 YouTube API 規定申請稽核。
