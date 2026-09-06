# 商用寫實咖啡館循環影片 Prompt Library

本提示詞庫配合 `WAN 2.2 FLF2V` 使用。每個場景都採用虛構咖啡館，不指定真實城市、地標、品牌或可辨識人物；請將同一張起始畫面上傳到 Start Frame 與 End Frame，讓畫面回到原始構圖形成循環。

## 這份文件與松獅犬流程的關係（2026-09-06 補充）

這 10 個場景是給**沒有狗的通用咖啡館循環**用的，走 `WAN 2.2 FLF2V`
（umT5 編碼器），不受 FLUX 的 token 預算限制，可以照舊使用。

松獅犬吉祥物那條線已經改成由 `config/scene_presets.yaml` +
`src/lyria_auto/studio/scene_composer.py` 組合，網頁頁籤 1 有可點擊的晶片
（場地／視野／季節／天氣／時間／姿勢／鏡頭），詳見
`chowchow-prompts.md`。**不要把下面這些長段落貼到關鍵幀的正向提示詞欄
位**——FLUX.1-schnell 的訓練序列長度只有 256 個 T5 token。

從那次修正搬得過來的三條寫法（下次擴充這份文件時可以照用）：

1. **用肯定敘述取代否定句。** `an empty pulled-out chair` 比
   `no person is visible` 有效——文字編碼器不擅長處理否定。
2. **物理接地要明講。** 接觸陰影、環境遮蔽、單一一致的光向、室內外亮度
   關係（白天窗外比室內亮約兩級）——這是「看起來像貼上去的」與「看起來
   真的在那裡」的差別。
3. **具體的攝影規格比形容詞有效。** `35mm at f/4, tripod at seated eye
   level` 勝過 `professional photo`。

另外，人類 2026-09-06 明確要求：**咖啡館要有一面看得到整片大自然**
（海邊、森林、山上、樹林、山景山水，或巴黎街頭那種戶外城市 view），
參考歐洲與日本咖啡館風格。下面第 4、6、10 個場景以外的室內場景，之後
擴充時應優先補上窗外／戶外景。品牌與地標規則不變：虛構、無可辨識地點。

## 共用 Negative Prompt

```text
blurry, low quality, worst quality, cartoon, illustration, CGI, 3D render, surreal, impossible geometry, unstable architecture, camera shake, zoom, fast camera movement, flicker, strobing, frozen motion, static motion, brand logo, trademark, readable sign, watermark, subtitle, text, celebrity, public figure, recognizable face, malformed hands, extra fingers, fused fingers, extra limbs, deformed face, bad anatomy, bad proportions, duplicate objects, oversaturated, overexposed, underexposed, cropped, out of frame
```

## 1. 虛構街角咖啡館・清晨

```text
A fictional independent corner cafe in a quiet residential neighborhood at early morning, realistic wood and stone interior, soft daylight through frosted glass, gentle steam rising from one ceramic coffee cup, a sheer curtain moving slightly in a natural draft, subtle reflections on the counter, documentary photography, photorealistic live-action look, physically plausible materials and motion, locked-off camera, stable wide composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 2. 雨窗咖啡館・午後

```text
A fictional neighborhood cafe viewed from inside through a rain-streaked window, warm practical lights balanced with cool gray daylight outside, small drops sliding slowly down the glass, gentle steam rising from a mug, a napkin edge moving slightly from indoor airflow, realistic condensation and soft reflections, photorealistic documentary style, quiet natural atmosphere, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 3. 木質咖啡館・午後陽光

```text
A fictional small cafe with a warm wood interior and a large side window, late afternoon sunlight moving softly across the table, dust particles floating naturally in a narrow sunbeam, steam curling from a fresh coffee, a potted plant leaf swaying gently, realistic shadows and lens exposure, true-to-life live-action photography, shallow depth of field, nearly locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 4. 溫室咖啡館・微風

```text
A fictional cafe inside a modest glass conservatory, soft overcast daylight, realistic condensation on the glass roof, a few leaves moving gently in a mild breeze, steam rising slowly from a ceramic cup, subtle changes in natural reflections, believable glass, wood and fabric textures, photorealistic live-action look, calm observational cinematography, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 5. 低光咖啡館・黃昏

```text
A fictional independent cafe at dusk, warm table lamps and realistic indirect window light, a small candle flame moving subtly without dramatic flicker, steam rising from a dark ceramic cup, a curtain shifting softly near the doorway, natural shadow falloff and physically plausible reflections, cinematic documentary photography, photorealistic live-action quality, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 6. 石牆咖啡館・冬日雪景

```text
A fictional cafe with a simple stone wall and a broad window, cool winter daylight outside with gentle snow falling in the distance, warm interior light on a wooden table, steam rising from hot chocolate, a small door bell moving slightly from a draft, realistic snow reflections and condensation, photorealistic live-action style, quiet natural motion, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 7. 手作烘焙咖啡館・出爐時刻

```text
A fictional artisan bakery cafe, a tray of plain pastries cooling on a wooden counter, gentle heat haze and steam rising naturally, a linen cloth edge shifting from a faint airflow, a small wall clock hand moving subtly, soft realistic overhead lighting, detailed food texture without advertising or packaging, photorealistic documentary image, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 8. 室內植物咖啡館・明亮上午

```text
A fictional plant-filled cafe with natural timber furniture and a bright window, leaves swaying gently from a quiet ventilation current, a small pool of sunlight shifting across a ceramic saucer, steam rising from coffee, realistic leaf shadows and subtle glass reflections, premium documentary photography, photorealistic live-action quality, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 9. 夜間咖啡館・街燈倒影

```text
A fictional quiet cafe at night, viewed from inside through a clean window with soft out-of-focus street light reflections, warm interior practical lighting, steam rising slowly from a cup, a curtain moving almost imperceptibly, realistic low-light exposure and natural sensor grain, restrained cinematic documentary style, photorealistic live-action look, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 10. 露台咖啡館・微風晴天

```text
A fictional cafe terrace enclosed by simple planters, bright but natural daylight, leaves and a linen napkin moving gently in a mild breeze, coffee steam dissipating slowly, realistic soft shadows on a textured table, subtle glass reflections, true-to-life documentary photography, photorealistic live-action quality, locked-off camera, stable composition, smooth continuous loop returning to the same composition, no logos, no readable text, no recognizable location, no identifiable people
```

## 使用原則

1. 正式輸出前先用 `workflows/cafe-flf2v-wan22-mps.json`（節點 9 暫時改成 768×432 的動作試拍解析度）確認構圖、手部、文字與循環接點。
2. 每次只改一到兩個動作描述，例如 `steam rising`、`rain sliding` 或 `leaves swaying`，避免同時加入大量運動。
3. 使用鎖定鏡位、穩定構圖與小幅度環境運動，避免生成看起來像模板批量內容的快速鏡頭。
4. 若畫面出現招牌、包裝或可識別人物，應重新生成或裁切，不要直接作為商用成片。
5. 將自己的旁白、環境音設計、剪輯節奏與場景敘事加入成片，降低重複性內容風險並提高原創價值。

## Motion Vocabulary

| English | 中文 |
|---|---|
| steam rising | 蒸氣上升 |
| rain sliding on glass | 雨滴沿玻璃滑落 |
| leaves swaying gently | 葉片輕柔搖曳 |
| curtain moving in a draft | 窗簾隨氣流移動 |
| dust floating in sunlight | 灰塵在陽光中漂浮 |
| candle flame moving subtly | 燭火細微晃動 |
| reflections shifting softly | 倒影柔和變化 |
| snow falling in the distance | 遠處雪花飄落 |
| linen edge lifting slightly | 亞麻布邊緣輕微掀動 |
| condensation forming on glass | 玻璃上形成凝結水珠 |
