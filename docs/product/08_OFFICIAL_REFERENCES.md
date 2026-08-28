# 官方技術依據（查核日期：2026-07-26）

- Google AI for Developers — Generate music with Lyria 3
  - Lyria 3 Clip：`lyria-3-clip-preview`，30 秒。
  - Lyria 3 Pro：`lyria-3-pro-preview`，完整歌曲，時長可由 Prompt 影響。
  - 建議使用 Interactions API。
- Google Cloud — Lyria music generation prompt guide
  - Lyria 具有安全過濾、recitation checking 與 artist intent checks。
- YouTube Data API — Videos: insert
  - 支援影片與 metadata 上傳、resumable media upload。
  - 排程時間需配合 private 狀態。
- YouTube Data API — Thumbnails: set
  - 自訂縮圖最大 2 MB，支援 JPEG/PNG。
- YouTube Data API — PlaylistItems: insert / Playlists: insert
  - 可自動建立播放清單並加入影片。
- YouTube OAuth guidance
  - 一般 YouTube 頻道不能使用 Service Account；常駐程式需先取得 offline refresh token。

官方文件：

- https://ai.google.dev/gemini-api/docs/music-generation
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/music/music-gen-prompt-guide
- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/docs/thumbnails/set
- https://developers.google.com/youtube/v3/docs/playlistItems/insert
- https://developers.google.com/youtube/v3/guides/moving_to_oauth
