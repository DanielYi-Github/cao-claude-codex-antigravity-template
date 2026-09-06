# 松獅犬吉祥物 Prompt Library

配合 `cafe-keyframe-flux-chowchow-lora-mps.json`（關鍵幀）與
`cafe-flf2v-wan22-mps.json`（動作）使用。

## 架構：從「手抄範本」改成「可點擊的預設選項」

第一版（合成流程）只有 1 個共用關鍵幀，狗是從
`approved/master-lying-transparent-v1.png` 這張固定去背圖貼上去的。
第二版改用 `chowchow_mascot` 角色 LoRA 直接在每個場景裡原生生成，這份
文件當時變成一組給人手抄的長提示詞範本。

第三版（2026-09-06，現在）把範本換成 `config/scene_presets.yaml` 的七個
軸——場地／視野／季節／天氣／時間／姿勢／鏡頭——由
`src/lyria_auto/studio/scene_composer.py` 組成完整提示詞，網頁頁籤 1 直接
點晶片就能帶入。原因見下一節：手抄範本膨脹到了模型根本讀不完的長度。

## 固定身份描述（由組合器自動放在最前面，不要改）

```text
One chow chow dog, chowchow_mascot: dense cinnamon-red fur, thick mane, black nose. Only one dog.
```

- `chowchow_mascot` 是訓練 LoRA 時用的觸發詞，一定要保留在句子裡。
  （注意：workflow 的 `LoraLoader` 是 `strength_clip: 0`，LoRA 的文字編碼器
  側其實是關閉的，所以這個詞目前沒有對應的學習嵌入——是否還需要保留值得
  實測，在有實測結果之前先不動。）
- 「Only one dog」不是裝飾——角色 LoRA 常見的失敗模式是畫面裡多長出第二
  隻狗，而 CFG 固定 1.0 的關係，negative prompt 在這個專案裡本來就無效，
  只能靠正向提示詞把它排除掉。
- 犬種細節對齊
  `character-reference/chowchow/approved/identity-notes-v1.md`。

## 共用 Negative Prompt

節點圖裡仍然接著 negative prompt（關鍵幀是 `stages.py` 的
`KEYFRAME_NEGATIVE_PROMPT`，動作是 `MOTION_NEGATIVE_PROMPT`），但
**CFG 固定為 1.0，這兩段文字都不會生效**，保留只是跟其他 workflow 的節點
圖一致。網頁頁籤 1 的負向提示詞欄位也標示了這一點。要排除的東西一律要
寫進正向提示詞。

## ⚠️ 這份文件現在是「參考輸出」，不是要手抄的範本

2026-09-06 起，關鍵幀與動作提示詞由 `config/scene_presets.yaml` +
`src/lyria_auto/studio/scene_composer.py` 組出來，網頁頁籤 1 有可點擊的
晶片（場地／視野／季節／天氣／時間／姿勢／鏡頭）。下面的範例是那個組合器
的實際輸出，貼在這裡供人閱讀與比對，**不要在這裡手動改措辭**——要改請改
`config/scene_presets.yaml`，那裡有測試把關。

### 為什麼整份重寫

舊版這份文件的提示詞實測是 **861 個 T5 token**，而 FLUX.1-schnell 的訓練
序列長度是 **256**。ComfyUI 的 T5XXLTokenizer 不會截斷，但遠離訓練分布的
結果是排在後段的指令實際上不被遵守——實測第 256 個 token 落在窗景那段
中間，也就是說「狗的位置與大小」「姿勢」「鏡頭與構圖」整整三段都在模型
會遵守的範圍之外。人類回報的「狗的位置不準、物理不真實」就是這麼來的。

另外四個獨立缺陷（詳見 `artifacts/spec.md`）：

1. 負向提示詞在 CFG=1.0 下完全無效，卻寫了 386 個字，形成假的控制感。
2. 正向提示詞用 9 行 `No X is visible` 這種否定句——文字編碼器不擅長否定，
   「no person」反而可能召喚出人。改成肯定敘述：`an empty pulled-out chair`。
3. 「approximately 10-15 percent of the total image height」——模型不會量測
   百分比。改成相對物理錨點：`two tables back`、`shoulder below the chair rail`。
4. 同一段提示詞給出「二選一」的兩種姿勢。模型不會執行 XOR，只會把兩種
   混成扭曲的姿態。現在一次只給一種。

### 現在的語序（刻意的，不要重排）

    身份 → 姿勢與地面接觸 → 相對家具的位置 → 場地與窗外景 → 季節
    → 天氣 → 光線 → 鏡頭 → 物理接地

前三段要塞進 CLIP-L 硬性截斷的 77 個 token（約 59 個英文字）裡，因為
CLIP-L 的 pooled 向量主導整體構圖。舊版把頻道行銷文案
（"a relaxing study-with-me and ambient jazz background scene…"）放在最前面，
等於把最寶貴的預算浪費掉。

### 光線是推導出來的，不是可以自由搭配的一軸

季節 × 天氣 × 時間決定光的色溫、方向、硬度與室內外亮度關係。如果讓人自由
組合，「冬天 + 下雪 + 黃金時刻 + 溫暖斜射陽光」這種物理上不可能的光就會被
組出來——那正是要修的問題本身。天氣屬於擴散類（陰／雨／霧／雪）時，時間軸
自動改用沒有方向性硬陽光的措辭；入夜後室內外亮度反轉，季節與天氣裡的太陽
高度描述也一併拿掉。

### 全部 39690 種組合都實測過

用 ComfyUI 自帶的 T5 tokenizer 逐一量測，真實 token 落在 **194–241**，
沒有任何一種組合超過 256。測試 `tests/test_scene_composer.py` 把這條釘住：
新增選項時如果失敗，正確的反應是去別處刪字，不是把上限調大。

## 場景範例（組合器的實際輸出）

### 預設：森林木屋・落地窗內側・秋・薄雲・午後・趴睡・35mm

晶片：森林木屋、落地窗內側、秋、薄雲、午後、趴睡、標準紀實　估算 234 tokens

```text
One chow chow dog, chowchow_mascot: dense cinnamon-red fur, thick mane, black nose. Only one dog. Lying flat on the wide plank floor, belly pressed against it, front paws forward, head resting on them, eyes half closed. Small in frame, two tables back, shoulder below the chair rail, beside an empty pulled-out chair and a table with a laptop and a steaming mug. A timber cabin cafe, full-height glass onto tall conifers and moss. Framed from inside the glass. Autumn, amber and rust foliage, a low sun. Thin high cloud, soft-edged shadows. Mid-afternoon, warm light angling across the floor. 35mm at f/4, tripod at seated eye level, locked off, deep focus. Soft contact shadow where it meets the ground, one light direction across dog and room, outside two stops brighter than in. Real wood grain, documentary photograph.
```

這是目前 64 秒巨集循環在用的場景意圖。

Motion 1（睡覺／維持姿勢）：

```text
A seamless 8-second loop from this image. The chow chow on the wide plank floor stays lying in the exact same pose, only its tail sweeps slowly and its ribs rise and fall with calm breathing. Everything else holds still: steam keeps curling from the mug and a leaf outside shifts slightly. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

Motion 2（抬頭看一眼，8 段裡只播 1 次）：

```text
A seamless 8-second loop from this image. The chow chow on the wide plank floor lifts its head from its paws, glances once toward the empty chair, then lowers its head back onto its paws exactly as it began. Everything else holds still: steam keeps curling from the mug and a leaf outside shifts slightly. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

### 湖畔・半開放露台・夏・晴・黃金時刻・坐著看主人・24mm

晶片：湖畔、半開放露台、夏、晴、黃金時刻、坐著看主人、廣角環境　估算 234 tokens

```text
One chow chow dog, chowchow_mascot: dense cinnamon-red fur, thick mane, black nose. Only one dog. Sitting upright and still on the plank floor at the open threshold, front paws planted square, facing the empty chair. Small in frame, two tables back, shoulder below the chair rail, beside an empty pulled-out chair and a table with a laptop and a steaming mug. A lakeside cafe, glass onto a still lake and low mountains beyond. The wall folded open to the terrace. Midsummer, dense dark-green foliage, a steep sun. Clear sky, hard-edged shadows. The hour before sunset, amber light, long shadows. 24mm at f/5.6, tripod at seated eye level, locked off, deep focus. Soft contact shadow where it meets the ground, one light direction across dog and room, outside two stops brighter than in. Real wood grain, documentary photograph.
```

Motion 1（睡覺／維持姿勢）：

```text
A seamless 8-second loop from this image. The chow chow on the plank floor at the open threshold stays sitting in the exact same pose, only its tail sweeps slowly behind it and its chest rises and falls with calm breathing. Everything else holds still: steam keeps curling from the mug and dust drifts in the sunlight. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

Motion 2（抬頭看一眼，8 段裡只播 1 次）：

```text
A seamless 8-second loop from this image. The chow chow on the plank floor at the open threshold turns its head slowly toward the view outside, holds the look briefly, then turns back to the empty chair exactly as it began. Everything else holds still: steam keeps curling from the mug and dust drifts in the sunlight. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

### 日式緣側・全戶外座位・春・薄雲・上午・蜷成一團・50mm

晶片：日式緣側、全戶外座位、春、薄雲、上午、蜷成一團、中距淺景　估算 236 tokens

```text
One chow chow dog, chowchow_mascot: dense cinnamon-red fur, thick mane, black nose. Only one dog. Curled into a tight circle on the worn stone terrace, tail tucked along its flank, nose in its own fur. Small in frame, two tables back, shoulder below the chair rail, beside an empty pulled-out chair and a table with a laptop and a steaming mug. An old Japanese house cafe on its engawa veranda, a moss garden with stepping stones and a maple. The table outdoors, only the eave overhead. Spring, pale new leaves. Thin high cloud, soft-edged shadows. Mid-morning, cool sunlight at a shallow angle. 50mm at f/2.8, tripod at seated eye level, locked off, soft background. Soft contact shadow where it meets the ground, one light direction across dog and room, outside two stops brighter than in. Real wood grain, documentary photograph.
```

Motion 1（睡覺／維持姿勢）：

```text
A seamless 8-second loop from this image. The chow chow on the worn stone terrace stays curled in the exact same pose, only its flank rises and falls with slow breathing and its fur shifts slightly. Everything else holds still: steam keeps curling from the mug and a leaf outside shifts slightly. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

Motion 2（抬頭看一眼，8 段裡只播 1 次）：

```text
A seamless 8-second loop from this image. The chow chow on the worn stone terrace raises its head briefly, opens its eyes toward the empty chair, then tucks its nose back into its fur exactly as it began. Everything else holds still: steam keeps curling from the mug and a leaf outside shifts slightly. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

### 巴黎街角・落地窗內側・冬・雪・藍調時刻・趴睡・35mm

晶片：巴黎街角、落地窗內側、冬、雪、藍調時刻、趴睡、標準紀實　估算 239 tokens

```text
One chow chow dog, chowchow_mascot: dense cinnamon-red fur, thick mane, black nose. Only one dog. Lying flat on the wide plank floor, belly pressed against it, front paws forward, head resting on them, eyes half closed. Small in frame, two tables back, shoulder below the chair rail, beside an empty pulled-out chair and a table with a laptop and a steaming mug. A corner cafe with tall French windows, a Haussmann stone street, zinc roofs, iron balconies. Framed from inside the glass. Winter, bare branches. Snow falling, lit by the windows. After sunset, deep blue outside against warm lamps inside. 35mm at f/4, tripod at seated eye level, locked off, deep focus. Soft contact shadow where it meets the ground, one lamp direction across dog and room, inside brighter than out, the glass faintly reflecting it. Real wood grain, documentary photograph.
```

巴黎只做風格（Haussmann 石造街屋、鋅皮屋頂、鍛鐵陽台），不放任何可辨識地標——沿用專案既有的「虛構、無可辨識地點」商用規則。

Motion 1（睡覺／維持姿勢）：

```text
A seamless 8-second loop from this image. The chow chow on the wide plank floor stays lying in the exact same pose, only its tail sweeps slowly and its ribs rise and fall with calm breathing. Everything else holds still: snow keeps falling slowly outside and steam curls from the mug. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

Motion 2（抬頭看一眼，8 段裡只播 1 次）：

```text
A seamless 8-second loop from this image. The chow chow on the wide plank floor lifts its head from its paws, glances once toward the empty chair, then lowers its head back onto its paws exactly as it began. Everything else holds still: snow keeps falling slowly outside and steam curls from the mug. The last frame matches the first exactly. Locked-off camera, no scene change, no new objects, photorealistic, physically plausible motion.
```

## 新增場景時的原則

1. **改 `config/scene_presets.yaml`，不要改這份文件。** 這裡是輸出快照。
2. **每段措辭控制在 15 個英文字以內。** 七個軸各多寫 30 個字就會回到 861。
3. 姿勢只收「安靜、可收尾回到起始姿勢」的靜態動作（趴、坐、蜷）。不要加
   「走路」——64 秒巨集循環要求結尾必須精確對齊開頭。
4. 新增場地一定要附 `view`（窗外／戶外景）。純室內的場地不該進這個清單。
5. 不要放任何可辨識的真實地標、品牌或人物。
6. 主人「完全不入鏡」跟「畫面只有一隻狗」一樣，只能靠正向措辭的寫法保證，
   不能靠 negative prompt（CFG=1.0 下無效）。
7. 每次新場景生成後，人工核對狗的毛色花紋、鬃毛、體型、尾巴是否還符合
   `identity-notes-v1.md`，LoRA 不保證每次都 100% 沒有漂移。

## 已知的限制（提示詞改寫解決不了的部分）

- **FLUX schnell 在 4 步、CFG=1.0 下對材質、光線、反射的寫實度有天花板。**
  這次的修正讓構圖與物理關係受控，但不會把 schnell 變成 dev。要評估換模型
  之前，必須先確認 `chowchow-identity-v2` LoRA 是在哪個 base 上訓練的。
- **`LoraLoader` 的 `strength_model: 0.65` 會抵抗遠景構圖。** v2 訓練集幾乎
  都是近景與中景，主體 LoRA 在這個強度下會把構圖拉回訓練時的取景，所以
  「狗在畫面裡很小」是被權重抵抗，不只是文字沒寫清楚。建議實測 0.45–0.55
  對遠景與身份保真度的影響。
- **`strength_clip: 0` 表示 LoRA 的文字編碼器側完全關閉**，`chowchow_mascot`
  沒有對應的學習嵌入，只是普通 subword，卻佔用 CLIP-L 的 77 token 預算。
  是否可以拿掉需要實測，目前保留。
