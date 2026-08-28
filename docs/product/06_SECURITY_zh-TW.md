# 安全與憑證管理

- `.env`、`secrets/`、`workspace/` 與 `logs/` 已列入 `.gitignore`。
- 不要把 `client_secret.json`、OAuth token 或 API key 上傳至 GitHub、雲端公開資料夾或聊天紀錄。
- OAuth token 等同頻道操作權限，應限制檔案權限。
- 多頻道請使用不同 token 檔，不要共用。
- Docker 部署時，使用 volume 掛載 secrets，不要 COPY 到 image。
- 預設上傳 privacy 為 private，完成測試後才考慮排程或公開。
- 程式不會關閉 Lyria 安全過濾，也不會嘗試規避版權或藝術家意圖檢查。
