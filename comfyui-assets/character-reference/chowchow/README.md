# 松獅犬主角角色參考包

這個目錄用來保存頻道固定主角的身份參考素材。目標不是只做一張漂亮的角色圖，而是建立一套能被 ComfyUI 工作流反覆使用的「角色資產包」，降低不同影片之間的犬種、毛色、臉部花紋與體型漂移。

## 你的照片放哪裡

請把實際飼養的松獅犬照片放在：

```text
comfyui-cafe-loop-generator/character-reference/chowchow/source/
```

這個 `source/` 資料夾已經加入 `.gitignore`，原始照片不會被 Git 追蹤。請保留原始檔名也可以；建議使用容易辨識的名稱，例如：

```text
source/
  front-standing-01.jpg
  left-profile-01.jpg
  right-profile-01.jpg
  three-quarter-face-01.jpg
  full-body-lying-01.jpg
  looking-up-01.jpg
```

建議先放 8–20 張，不需要每張都完美，但最好涵蓋：

- 正面、左右側面、3/4 角度、背面或尾巴
- 全身站立、趴臥、抬頭等常見姿勢
- 自然光、無濾鏡、沒有嚴重遮擋臉部或毛色的照片
- 能看清楚鬃毛、耳朵、尾巴、腳掌與身體比例的照片

如果項圈、吊牌、蝴蝶結等配件要成為主角固定造型，也請保留幾張帶配件的照片；否則第一版會把配件視為非固定元素。

## 目錄用途

```text
source/     你的原始照片；私人資料，預設不進 Git
working/    接觸表、挑選結果、生成草稿；預設不進 Git
approved/   核准後的生產素材，供 ComfyUI 工作流使用
```

## 後續製作流程

1. **照片盤點**：檢查哪些照片能辨識真實狗狗的固定特徵，排除濾鏡、動態模糊和嚴重遮擋。
2. **角色設定圖**：以照片作為身份參考，製作商業寫實、略帶動畫設計感的 model sheet，包含正面、側面、3/4、趴姿、抬頭與臉部特寫。
3. **人工核准**：確認毛色、臉部花紋、鬃毛、耳朵、尾巴、體型和眼睛都像真實狗狗。未核准前不接入影片生成。
4. **主參考圖**：從設定圖中挑出一張乾淨、完整、光線中性的主參考圖，另準備 3/4 和趴姿參考圖，放進 `approved/`。
5. **表情參考**：若場景需要開心、迎接客人或看鏡頭，使用核准的 `smiling-face-v2.png` 與透明 cutout；表情沿用自然張嘴、吐出藍紫色舌頭的樣子。
6. **身份描述**：整理一份固定描述，之後所有咖啡店場景共用；場景提示詞只改咖啡店、時間、天氣和動作，不改狗狗身份特徵。
7. **接入關鍵幀流程**：目前 Studio/API 路徑使用 `workflows/api/cafe-keyframe-flux-chowchow-lora-mps.json`，用訓練好的 `chowchow_mascot` 角色 LoRA 直接在每個場景裡原生生成狗狗，不是拿固定的去背圖去貼。
8. **接入動作流程**：關鍵幀核准後，繼續沿用 `cafe-flf2v-wan22-mps.json`；同一張狗狗關鍵幀同時作為 Start Frame 和 End Frame，維持每個 8 秒片段的身份與循環姿勢。
9. **升頻**：最後才使用 `cafe-upscale-1080p-mps.json`，只放大已核准影片，不重新生成動作。

## 與目前工作流的整合原則

目前的影片流程是：

```text
角色 LoRA + 場景/姿勢提示詞（chowchow-prompts.md）
        ↓
FLUX 關鍵幀候選（狗與場景同一次生成，光影原生一致）
        ↓ 人工核准
WAN 2.2 First/Last Frame 動畫
        ↓ 人工核准
ESRGAN 後製升頻到 1080p
```

**這條路徑取代了先前「FLUX 生成背景 + 貼上固定去背圖 + ColorTransfer 輕微調色」的合成流程**（舊檔案 `workflows/api/cafe-keyframe-flux-chowchow-composite-mps.json` 仍保留在 repo 裡作為 fallback，沒有被刪除，但不再是預設路徑）。換掉的原因：合成流程只能做全域色彩統計校正，沒辦法重新打光、生成正確方向的接觸陰影，也沒辦法匹配背景的景深/銳利度，所以狗在畫面裡永遠有「貼紙感」；而且因為只能貼預先棚拍好的那幾張固定姿勢，狗的動作也被鎖死在那幾種姿勢上，沒辦法隨著新場景自然變化。角色 LoRA 是用 `source/` 裡狗狗的真實生活照/影片訓練的（不是用 `approved/` 裡棚拍風格的 AI 生成參考圖，避免把棚燈的打光風格也一起學進去），讓狗直接在每個新場景裡跟背景同一次生成，姿勢由 `chowchow-prompts.md` 的文字描述決定。

角色設定圖（`approved/master-*.png` 等）現在的角色轉為：人工核對新生成畫面的身份特徵（毛色、臉型、鬃毛、尾巴、體型）是否還符合 `identity-notes-v1.md` 的規格參考，不再是生成流程裡實際會被合成進畫面的素材。

真正降低跨場景漂移的關鍵：

- 每次生成的 prompt 開頭都固定接同一段身份描述 + `chowchow_mascot` 觸發詞（見 `chowchow-prompts.md`）
- 以咖啡桌、椅子、地板等固定物件校準場景比例
- 先核准一張關鍵幀，再用它驅動後續動作，不在影片階段重新生成狗狗外觀
- 每次生成後人工檢查臉部花紋、鬃毛、體型與尾巴——LoRA 不保證每次都 100% 沒有漂移，跟角色設定圖比對是必要的複檢步驟

## 建立 LoRA v3 私有資料集

v3 的可追蹤選片清單在 `lora-training-set-v3-manifest.json`；照片本身仍位於
被 Git 忽略的 `source/`。在專案根目錄執行：

```bash
python3 comfyui-assets/scripts/build_chowchow_lora_dataset_v3.py
```

腳本會建立 `source/lora-training-set-v3/`，複製 26 張精選照片並產生統一的
身份 caption。若要重新建立，使用 `--force`。完成後將該目錄上傳成私有 Kaggle
dataset `danielyiyi/chowchow-mascot-lora-dataset-v3`，再推送訓練 kernel。

2026-09-02 的 v3 訓練已在 Kaggle 完成 1,000 steps，輸出檔名為
`chowchow-identity-v3.safetensors`。本機固定 8-seed 壓力測試比較 v2 0.50 與
v3 0.35／0.50／0.65 後，關鍵幀預設採用 v3 0.35；較高強度偶爾會把收起的後腳
畫成視覺上像額外肢體。預設 prompt 也要求狗狗全身不被家具遮住、採明確三分之四
側面趴姿，讓產生候選圖時更容易做解剖檢查。

目前 MPS 關鍵幀仍使用 `flux1-schnell-Q4_K_S.gguf`，但 v3 LoRA 是以 FLUX.1-dev
訓練；這是已知的底模不匹配。上述 0.35 是目前 Schnell fallback 的實測設定，並不
代表完全解決底模相容性。要追求更高的角色一致性，下一步應安裝可接受授權條款的
FLUX.1-dev 基礎模型，另建 dev workflow 後重新做相同 seed 的 A/B，不能直接把目前
workflow 的檔名改成 dev 就宣稱完成。

## 暫不放進這個目錄的東西

- 不要把多隻狗混在同一批身份參考裡。
- 不要把帶有不同年齡、不同毛長或不同體型的狗狗照片混用，除非那些變化本來就是同一隻狗的固定外觀。
- 不要直接把未核准的生成圖當作新的身份參考，否則模型漂移會被逐代放大。
