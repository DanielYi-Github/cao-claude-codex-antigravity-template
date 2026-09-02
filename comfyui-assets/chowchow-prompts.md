# 松獅犬吉祥物 Prompt Library

配合 `cafe-keyframe-flux-chowchow-lora-mps.json`（關鍵幀）與 `cafe-flf2v-wan22-mps.json`（動作）使用。

## 架構：從「1 個共用關鍵幀」改成「固定身份 + 場景範例庫」

舊版（合成流程）只有 1 個共用關鍵幀，因為狗是從 `approved/master-lying-transparent-v1.png` 這張固定去背圖貼上去的——不管想生成哪個場景，畫面裡的狗永遠是同一張圖、同一個姿勢。

現在關鍵幀改用訓練好的 `chowchow_mascot` 角色 LoRA 直接在每個場景裡原生生成，狗的姿勢、角度、光影都是那次生成的一部分，不再被單一張去背圖綁死。所以這份文件的結構比照 `prompt-library.md` 那 10 個通用咖啡館場景——**1 段固定身份描述 + 一組可以持續擴充的場景/姿勢範例**，不是「10 選 1」也不是「1 個共用」，未來可以視需要一直加新的場景進來。

每個新場景實際上是透過 `Lyria-Auto-Publisher` 那邊每個 episode 自己的 `source_prompt` 帶進去的（`stages.py` 的 `generate_keyframe()` 本來就是 `asset["source_prompt"] or DEFAULT_KEYFRAME_PROMPT`）——這份文件是給人看、手動組新場景 prompt 時參考用的範本庫。

## 固定身份描述（每個場景開頭都要有，不要改）

```text
Exactly one adult chow chow dog, chowchow_mascot, with fluffy reddish-brown fur, thick rounded mane, black nose, small triangular ears, and a sturdy compact body.
```

- `chowchow_mascot` 是訓練 LoRA 時用的觸發詞，一定要保留在句子裡。
- 「Exactly one adult chow chow dog」不是裝飾——角色 LoRA 常見的失敗模式是畫面裡多長出第二隻狗，而且 CFG 固定 1.0 的關係，negative prompt 在這個專案裡本來就是無效的（見下方說明），只能靠正向提示詞約束，不能指望 negative prompt 排除。
- 犬種細節（暖金紅／橘棕色、厚鬃毛、黑鼻、結實緊湊體型）對齊 `character-reference/chowchow/approved/identity-notes-v1.md`，跟場景無關的部分不要換。

## 共用 Negative Prompt（關鍵幀）

沿用 `prompt-library.md` 的共用 negative prompt（CFG=1.0 下這段文字本來就不生效，保留只是跟其他 workflow 的節點圖一致）：

```text
blurry, low quality, worst quality, cartoon, illustration, CGI, 3D render, surreal, impossible geometry, unstable architecture, camera shake, zoom, fast camera movement, flicker, strobing, frozen motion, static motion, brand logo, trademark, readable sign, watermark, subtitle, text, celebrity, public figure, recognizable face, malformed hands, extra fingers, fused fingers, extra limbs, deformed face, bad anatomy, bad proportions, duplicate objects, oversaturated, overexposed, underexposed, cropped, out of frame
```

## 共用 Negative Prompt（動作）

```text
camera shake, zoom, pan, fast camera movement, scene transition, flicker, strobing, frozen motion, static motion, object morphing, changing layout, new objects, disappearing objects, warped geometry, duplicate objects, blurry, low quality, worst quality, oversaturated, overexposed, underexposed, cropped, out of frame
```

## 場景範例 1：暮色湖畔景觀咖啡廳・木地板趴姿（目前 64 秒巨集循環在用的場景）

```text
High-end architectural editorial photography of a luxurious biophilic destination cafe integrated into a spectacular natural landscape at blue hour. An extra-wide 16:9 establishing view from deep inside an open-sided lakeside pavilion looks outward through a wall-sized opening toward a calm mountain lake, forested cliffs, misty peaks, and mature trees. The lake and wilderness are the main subject and command at least sixty percent of the visual attention. Natural timber beams, pale stone, a bare wooden floor, an espresso bar, linen lounge seating, low wood tables, and a few elegant cafe tables frame the foreground and sides without blocking the view. Subtle amber lanterns and warm practical lights inside balance the cool blue evening landscape outside, creating a tranquil premium jazz-cafe ambience without a stage or performers. The entire cafe is empty except for exactly one adult chow chow dog, chowchow_mascot, with fluffy reddish-brown fur, thick rounded mane, black nose, small triangular ears, and a sturdy compact body. The dog rests directly on the bare wooden floor in the middle distance near the lower-left third, occupying only about twenty to twenty-five percent of the image width and remaining secondary to the scenery. Bare wood planks are visible beneath and around its paws and belly; no rug, mat, carpet, blanket, cushion, mattress, or dog bed is beneath it. The unobstructed dog has one continuous body and exactly one head, seen in a natural three-quarter side view, lying calmly with two separate front paws, four anatomically correct legs total, and one curled tail. No furniture overlaps the dog. No people, human figures, portraits, laptop, bookshelves, library, urban skyline, signs, logos, watermark, or readable text. Photorealistic natural materials, deep spatial composition, locked-off cinematic camera, peaceful long-form ambience.
```

這個場景以湖泊、森林、山壁與室內外連續的建築景觀為主角，參考高級自然系 destination cafe，而不是傳統室內咖啡館。狗狗只佔畫面寬度約 20–25%，木石咖啡廳作為前景框景，戶外景觀至少取得六成視覺注意力。用藍調暮色搭配室內琥珀暖燈，保留深夜伴讀氣氛但避免封閉酒吧感。Schnell 不會精準量測百分比，因此每批候選仍要人工檢查。

搭配這個場景的兩組動作 prompt（64 秒巨集循環 = 8 個 8 秒片段，前 7 個播 Motion 1、第 8 個播 Motion 2）：

### Motion 1 — 睡覺搖尾巴（64 秒巨集循環裡播 7 次）

```text
A continuous seamless 8-second loop video based on the image. The fluffy chow chow dog remains lying flat on the cafe floor in the exact same pose throughout the clip, only its tail wags gently a few times and its chest and back rise and fall slowly with calm breathing, fur shifting subtly. Ambient environmental motion only: steam continues curling gently from the coffee mug on the table, soft daylight shifts almost imperceptibly. The owner stays completely out of frame throughout, only the chair, laptop, and mug are visible. The dog's head and body position at the end of the clip matches the very first frame exactly. No camera movement, no scene change, no new objects, smooth continuous loop returning to the same composition, photorealistic, physically plausible motion
```

### Motion 2 — 抬頭看一眼（64 秒巨集循環裡只播 1 次）

```text
A continuous seamless 8-second loop video based on the image. The fluffy chow chow dog is lying flat on the cafe floor. Partway through the clip, the dog slowly lifts its head up from its front paws and turns to glance toward the empty chair and table where its owner would be sitting, holds the glance for a brief moment, then gently lowers its head back down onto its front paws and closes its eyes, returning to the exact same resting pose as the very first frame. The owner remains completely out of frame throughout, only the chair, laptop, and steaming mug are visible. Subtle ambient motion: soft daylight shifting gently, steam rising from the mug. No camera movement, no scene change, no new objects, smooth continuous loop where the end frame connects seamlessly back to the start frame, photorealistic, physically plausible motion
```

**為什麼「抬頭看一眼」8 段裡只出現 1 次，不是每段都有**：如果每 8 秒都重複同一個「抬頭」動作，兩小時的最終影片裡這個動作會播上百次，看起來會很機械、很像循環動畫在跑迴圈，反而更容易被看穿是循環素材。只放 1 次讓它像是偶發的真實反應，其餘 7 段維持最單純的睡覺搖尾巴。

## 新增場景時的原則

1. 開頭一定接固定身份描述（見上），中間換掉的只有姿勢/場景/光線那句。
2. 新場景如果要拿來做 64 秒巨集循環的關鍵幀，姿勢要挑「安靜、可收尾回到起始姿勢」的動作（例如趴著、坐著、蜷起來），不要挑「行走」這種難以在同一張圖上定義清楚起訖姿勢的動作——這是配合循環影片「结尾必须精确对齐开头」的硬性要求，不是所有姿勢都適合。如果只是要生成單張圖或不需要無縫循環的素材，這條限制不適用。
3. 每次新場景生成後，人工核對狗的毛色花紋、鬃毛、體型、尾巴是否還符合 `identity-notes-v1.md`，LoRA 不保證每次都 100% 沒有漂移。
4. 主人「完全不入鏡」跟「畫面只有一隻狗」一樣，只能靠正向提示詞的寫法保證，不能靠 negative prompt（CFG=1.0 下無效）。
