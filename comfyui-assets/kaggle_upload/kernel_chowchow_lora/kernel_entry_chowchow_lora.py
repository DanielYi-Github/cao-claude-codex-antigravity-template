"""Kaggle kernel 進入點：訓練松獅犬吉祥物角色 LoRA。
透過 kernel-metadata.json 的 dataset_sources 掛載：
- danielyiyi/chowchow-mascot-lora-dataset（18 張真實生活照/影片截圖 + caption）

用 ai-toolkit（https://github.com/ostris/ai-toolkit）訓練。原本走
FLUX.1-schnell + 官方 assistant adapter（ostris/FLUX.1-schnell-training
-adapter）這條路，因為 schnell 是蒸餾過的少步數模型、需要這個 adapter
在訓練時暫時「還原」成可訓練的多步驟模型，理論上能跟本機 ComfyUI 現有的
flux1-schnell-Q4_K_S.gguf + CFG 1 + 4-step 推論方式完全對齊。但這個 adapter
的「融合」步驟本身極度吃顯存（ai-toolkit 官方註解自己都寫「low_vram 融合
adapter 時會很慢」），在 T4 16GB 上一路排除掉其他瓶頸後，最後卡在這一步
過不去。改訓練 **FLUX.1-dev**（不蒸餾，不需要 assistant adapter，直接繞開
這個痛點）；代價是訓練出的 LoRA 要套在本機 schnell 推論上，身份保留度不一定
跟同模型訓練/推論一樣完美，但社群經驗上通常可接受，且 comfyui 那邊本來就有
「如果 schnell 效果不夠就切 FLUX.1-dev 推論」的備案（見 chowchow-integration
-plan.md）。

HF token 原本想透過 Kaggle Secrets 注入，但 Kaggle 有個已知限制：透過網頁
Add-ons -> Secrets 掛的 secret，只在網頁編輯器互動執行時有效，CLI
`kaggle kernels push` 觸發的執行完全讀不到（社群多次回報同樣的
ConnectionError / HTTP 400，官方目前無法在 kernel-metadata.json 指定
secrets）。改用跟訓練照片一樣的機制：token 放進一個獨立的私有 dataset
（danielyiyi/chowchow-lora-hf-token，只有一個 hf_token.txt），透過
dataset_sources 掛載進來直接讀檔案，不寫死在程式碼裡。FLUX.1-dev 在
Hugging Face 是 gated model，這個 token 需要先登入 HF 帳號接受授權條款
（已確認這個 token 對 dev 也有存取權限，不用重新申請）。

T4 只有 16GB VRAM，`patch_ai_toolkit_for_t4()` 修了三個 ai-toolkit 本身的
問題（過程記錄見對話紀錄，這裡只寫結論）：
1. **T5 全精度載入瞬間爆掉**：文字編碼器一定會先以全精度（bf16，
   T5-XXL ~9.5GB）搬上跟已量化的 transformer 同一張卡，量化「之後」才會
   縮小；改成 transformer 量化完先丟回 CPU 讓 T5 用滿整張卡載入+量化，
   量化完再搬回 GPU，兩個重的模型錯開時間點。
2. **`qtype_te` 是死設定**：T5 的 `quantize()` 呼叫寫死用
   `self.model_config.qtype`（transformer 的量化精度），根本沒讀
   `qtype_te`；patch 改成真的讀 `qtype_te`。
3. **`BaseSDTrainProcess.py` 在 `load_model()` 跑完之後，又對已經量化好的
   transformer 呼叫一次 `unet.to(self.device_torch, dtype=dtype)`，明確
   指定 `dtype=bf16`**——這行不管訓練的是 schnell+adapter 還是 dev，都在
   模型載入完成後準時的同一個時間點炸掉同樣的量，證明先前懷疑的「adapter
   融合很吃顯存」是誤判（換成 dev、拿掉 adapter 之後這個 OOM 完全沒變）。
   合理懷疑是這個明確的 dtype 轉換把量化過的凍結權重解量化回全精度
   （12B 參數的 bf16 版本，~24GB），不管量化精度調多低都沒用，因為問題
   不是「常駐大小」而是這行本身在嘗試臨時把它還原。patch 成：模型有量化時
   只做裝置搬移、不帶 dtype 參數。
`model.qtype` / `qtype_te` 都設 `"uint4"`（transformer 和文字編碼器都降到
4-bit，換取足夠的顯存餘裕），畫質可能受影響，等真的跑出結果再評估。
`model.te_device` 這個設定經查證是 flux2/wan21 等新架構才有接上的死設定，
對這裡的 FLUX.1 完全沒用，沒有採用。

4. **v21-v23 三次都在 `cache_text_embeddings()` 第一次 T5 forward 時，以
   完全相同的方式 OOM（80MiB 差、mem_allocated 14.29GB）**：拿到 v23 的
   完整 traceback 後，加上 Codex（透過 CAO 派工讀原始碼）跟顧問
   （advisor）的協助，才確認真正原因——T5 確實有量化成功（log 裡沒有任何
   `Failed to quantize` 訊息），也確實走的是 ai-toolkit 自己的 UIntX
   量化器（不是 optimum-quanto，`qtype_te="uint4"` 這個字串本來就不會解析
   成 quanto 的型別，網路上關於 quanto 在 Turing/T4 退回慢速解量化的說法
   在這裡不適用）。真正問題出在 `toolkit/util/uintx_quant.py` 的
   `forward()`：
     with torch.no_grad():
         w = self._dequantize_native(module)
     return torch.nn.functional.linear(x, w, module.bias)  # 在 no_grad 外面
   解量化本身包在 `no_grad` 裡，但呼叫 `linear()` 沒有。只要外層的 forward
   呼叫鏈（`encode_prompts_flux()`）本身不是在 `no_grad` 底下跑，autograd
   就會建圖、把每一層解量化出來的全精度權重存起來準備反向傳播——T5-XXL
   24 層，每層 attn(4×16.8M) + FFN(3×41.9M) ≈ 193M 參數、386MB（bf16），
   24 層加總 ≈ 9.26GB，跟量到的落差（3.22GB 進場、OOM 前 14.29GB）幾乎
   完全對得上，連炸掉時要求的 80MiB 都正好等於一個 wi_0 矩陣的大小。這個
   forward 只在「幫 caption 建快取」跟「產生預覽圖」這兩個不需要反向傳播
   的地方被呼叫（訓練設定裡 `train_text_encoder: false`，log 也印出
   `create LoRA for Text Encoder: 0 modules`），所以在 `encode_prompts_flux`
   外面包一層 `@torch.no_grad()` 是安全的——修在這裡而不是修
   `uintx_quant.py` 的 `linear()` 本身，是因為那裡是量化器的通用路徑，
   訓練時梯度需要真的穿過凍結的量化層才能傳到上游的 LoRA adapter，把那裡
   包死會直接讓訓練本身壞掉。
"""
import glob
import os
import shutil
import subprocess
import sys

AI_TOOLKIT_DIR = "/kaggle/working/ai-toolkit"
CONFIG_PATH = "/kaggle/working/chowchow_lora_config.yaml"
OUTPUT_NAME = "chowchow_mascot_v1"
FINAL_SAFETENSORS = "/kaggle/working/chowchow-identity-v1.safetensors"

CONFIG_YAML = """
job: extension
config:
  name: "{output_name}"
  process:
    - type: 'sd_trainer'
      training_folder: "/kaggle/working/output"
      device: cuda:0
      trigger_word: "chowchow_mascot"
      network:
        type: "lora"
        linear: 8
        linear_alpha: 8
      save:
        dtype: float16
        save_every: 500
        max_step_saves_to_keep: 2
        push_to_hub: false
      datasets:
        - folder_path: "{dataset_dir}"
          caption_ext: "txt"
          caption_dropout_rate: 0.05
          shuffle_tokens: false
          cache_latents_to_disk: true
          resolution: [512]
      train:
        batch_size: 1
        steps: 2000
        gradient_accumulation_steps: 1
        train_unet: true
        train_text_encoder: false
        gradient_checkpointing: true
        cache_text_embeddings: true
        unload_text_encoder: true
        noise_scheduler: "flowmatch"
        optimizer: "adamw8bit"
        optimizer_params:
          is_paged: true
        lr: 1e-4
        ema_config:
          use_ema: false
        dtype: bf16
      model:
        name_or_path: "black-forest-labs/FLUX.1-dev"
        is_flux: true
        quantize: true
        low_vram: true
        qtype: "uint4"
        qtype_te: "uint4"
      sample:
        sampler: "flowmatch"
        sample_every: 500
        sample_start_step: 0
        width: 512
        height: 512
        prompts:
          - "chowchow_mascot lying on a wooden cafe floor, warm window light, photorealistic"
          - "chowchow_mascot sitting on a rug indoors, soft daylight, photorealistic"
          - "chowchow_mascot standing outdoors in a park, natural daylight, photorealistic"
        neg: ""
        seed: 42
        walk_seed: true
        guidance_scale: 4
        sample_steps: 20
meta:
  name: "[name]"
  version: '1.0'
"""


def find_dir(pattern):
    matches = [m for m in glob.glob(pattern, recursive=True) if os.path.isdir(m)]
    assert matches, f"找不到符合 {pattern} 的資料夾，目前 /kaggle/input 內容：{os.listdir('/kaggle/input')}"
    return matches[0]


def run(cmd, **kwargs):
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def patch_ai_toolkit_for_t4():
    """toolkit/stable_diffusion_model.py always loads T5 onto the same
    device as the (already-quantized) transformer at full bf16 precision,
    quantizing it in place only afterward -- so there's an unavoidable
    moment where a quantized ~12B-param transformer and a full-precision
    T5-XXL (~9.5GB) are both resident at once. That's what OOMs a 14.56GB
    T4 regardless of LoRA rank/resolution/te_device (confirmed te_device is
    dead code for classic FLUX.1 in this file -- it's only wired up for the
    newer flux2/wan21/etc. model classes). Patch: move the transformer to
    CPU right before T5 loads, move it back once T5 is quantized down.
    """
    _patch_stable_diffusion_model()
    _patch_base_sd_train_process()
    _patch_dataloader_mixins()
    _patch_train_tools()


def _patch_stable_diffusion_model():
    path = os.path.join(AI_TOOLKIT_DIR, "toolkit", "stable_diffusion_model.py")
    with open(path) as f:
        src = f.read()

    if "chowchow patch" in src:
        print("patch_ai_toolkit_for_t4: stable_diffusion_model.py already patched, skipping")
        return

    anchor_a = (
        "            else:\n"
        "                transformer.to(self.device_torch, dtype=dtype)\n"
        "\n"
        "            flush()\n"
        "\n"
        "            scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(base_model_path, subfolder=\"scheduler\")"
    )
    replacement_a = (
        "            else:\n"
        "                transformer.to(self.device_torch, dtype=dtype)\n"
        "\n"
        "            flush()\n"
        "            transformer.to('cpu')  # chowchow patch: free the T4 for T5's full-precision load\n"
        "            flush()\n"
        "\n"
        "            scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(base_model_path, subfolder=\"scheduler\")"
    )
    assert anchor_a in src, "patch_ai_toolkit_for_t4: anchor_a not found, upstream file changed"
    src = src.replace(anchor_a, replacement_a, 1)

    # Upstream bug (not ours to fix generally, just working around it here):
    # the T5 quantize call hardcodes `self.model_config.qtype` -- the
    # transformer's quantization type -- instead of reading `qtype_te`, so
    # qtype_te is silently ignored for classic FLUX.1 no matter what the
    # config says. Same class of dead-config issue as te_device.
    anchor_b = (
        "            if self.model_config.quantize_te:\n"
        "                self.print_and_status_update(\"Quantizing T5\")\n"
        "                quantize(text_encoder_2, weights=get_qtype(self.model_config.qtype))\n"
        "                freeze(text_encoder_2)\n"
        "                flush()\n"
        "                \n"
        "            self.print_and_status_update(\"Loading CLIP\")"
    )
    replacement_b = (
        "            if self.model_config.quantize_te:\n"
        "                self.print_and_status_update(\"Quantizing T5\")\n"
        "                print(f'chowchow patch: quantizing T5 with qtype_te={self.model_config.qtype_te!r}"
        " (was hardcoded to qtype={self.model_config.qtype!r})')\n"
        "                quantize(text_encoder_2, weights=get_qtype(self.model_config.qtype_te))"
        "  # chowchow patch: use qtype_te, not qtype\n"
        "                freeze(text_encoder_2)\n"
        "                flush()\n"
        "            print(f'chowchow patch: pre-restore mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB"
        " mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
        "            transformer.to(self.device_torch)  # chowchow patch: T5 is quantized/small now, bring transformer back\n"
        "            flush()\n"
        "                \n"
        "            self.print_and_status_update(\"Loading CLIP\")"
    )
    assert anchor_b in src, "patch_ai_toolkit_for_t4: anchor_b not found, upstream file changed"
    src = src.replace(anchor_b, replacement_b, 1)

    # v16 got past the anchor_b restore (uint4 transformer fits) but OOMed
    # ~40s later, somewhere between here and the end of this block. Instrument
    # every step so the next run pinpoints it instead of guessing again.
    anchor_c = (
        "            self.print_and_status_update(\"Loading CLIP\")\n"
        "            text_encoder = CLIPTextModel.from_pretrained(base_model_path, subfolder=\"text_encoder\", torch_dtype=dtype)\n"
        "            tokenizer = CLIPTokenizer.from_pretrained(base_model_path, subfolder=\"tokenizer\", torch_dtype=dtype)\n"
        "            text_encoder.to(self.device_torch, dtype=dtype)\n"
        "\n"
        "            self.print_and_status_update(\"Making pipe\")\n"
        "            Pipe = FluxPipeline\n"
        "            \n"
        "            pipe: Pipe = Pipe(\n"
        "                scheduler=scheduler,\n"
        "                text_encoder=text_encoder,\n"
        "                tokenizer=tokenizer,\n"
        "                text_encoder_2=None,\n"
        "                tokenizer_2=tokenizer_2,\n"
        "                vae=vae,\n"
        "                transformer=None,\n"
        "            )\n"
        "            pipe.text_encoder_2 = text_encoder_2\n"
        "            pipe.transformer = transformer\n"
        "\n"
        "            self.print_and_status_update(\"Preparing Model\")\n"
        "\n"
        "            text_encoder = [pipe.text_encoder, pipe.text_encoder_2]\n"
        "            tokenizer = [pipe.tokenizer, pipe.tokenizer_2]\n"
        "\n"
        "            pipe.transformer = pipe.transformer.to(self.device_torch)\n"
        "\n"
        "            flush()\n"
        "            text_encoder[0].to(self.device_torch)\n"
        "            text_encoder[0].requires_grad_(False)\n"
        "            text_encoder[0].eval()\n"
        "            text_encoder[1].to(self.device_torch)\n"
        "            text_encoder[1].requires_grad_(False)\n"
        "            text_encoder[1].eval()\n"
        "            pipe.transformer = pipe.transformer.to(self.device_torch)\n"
        "            flush()\n"
    )
    def _mem(label):
        return (
            f"            print(f'chowchow patch: {label} "
            "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
            "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
        )
    replacement_c = (
        "            self.print_and_status_update(\"Loading CLIP\")\n"
        "            text_encoder = CLIPTextModel.from_pretrained(base_model_path, subfolder=\"text_encoder\", torch_dtype=dtype)\n"
        "            tokenizer = CLIPTokenizer.from_pretrained(base_model_path, subfolder=\"tokenizer\", torch_dtype=dtype)\n"
        "            text_encoder.to(self.device_torch, dtype=dtype)\n"
        + _mem("after CLIP to device") +
        "\n"
        "            self.print_and_status_update(\"Making pipe\")\n"
        "            Pipe = FluxPipeline\n"
        "            \n"
        "            pipe: Pipe = Pipe(\n"
        "                scheduler=scheduler,\n"
        "                text_encoder=text_encoder,\n"
        "                tokenizer=tokenizer,\n"
        "                text_encoder_2=None,\n"
        "                tokenizer_2=tokenizer_2,\n"
        "                vae=vae,\n"
        "                transformer=None,\n"
        "            )\n"
        "            pipe.text_encoder_2 = text_encoder_2\n"
        "            pipe.transformer = transformer\n"
        + _mem("after pipe built") +
        "\n"
        "            self.print_and_status_update(\"Preparing Model\")\n"
        "\n"
        "            text_encoder = [pipe.text_encoder, pipe.text_encoder_2]\n"
        "            tokenizer = [pipe.tokenizer, pipe.tokenizer_2]\n"
        "\n"
        "            pipe.transformer = pipe.transformer.to(self.device_torch)\n"
        + _mem("after 1st redundant transformer.to") +
        "\n"
        "            flush()\n"
        "            text_encoder[0].to(self.device_torch)\n"
        "            text_encoder[0].requires_grad_(False)\n"
        "            text_encoder[0].eval()\n"
        "            text_encoder[1].to(self.device_torch)\n"
        "            text_encoder[1].requires_grad_(False)\n"
        "            text_encoder[1].eval()\n"
        + _mem("after text_encoder[0/1] redundant .to") +
        "            pipe.transformer = pipe.transformer.to(self.device_torch)\n"
        "            flush()\n"
        + _mem("after 2nd redundant transformer.to (end of is_flux block)")
    )
    assert anchor_c in src, "patch_ai_toolkit_for_t4: anchor_c not found, upstream file changed"
    src = src.replace(anchor_c, replacement_c, 1)

    with open(path, "w") as f:
        f.write(src)
    print("patched toolkit/stable_diffusion_model.py for T4 (transformer<->CPU swap around T5 load)")


def _patch_base_sd_train_process():
    """v18 (FLUX.1-dev, no assistant adapter) still OOMed ~24s after the
    is_flux block finished at a comfortable 9.56GB, with the exact same
    numbers as v17 (schnell+adapter) -- proving the adapter fusion was
    never the cause. jobs/process/BaseSDTrainProcess.py calls
    `unet.to(self.device_torch, dtype=dtype)` right after load_model()
    returns, explicitly casting the already-quantized transformer to
    bf16 -- if the quantized tensor type doesn't ignore dtype= casts,
    this dequantizes a frozen ~12B-param model back to full precision
    (~24GB), which is a plausible match for the spike. Patch: skip the
    dtype= kwarg (device-only move) when the model is quantized, plus a
    memory print right after so this either confirms the theory or rules
    it out with hard numbers instead of another guess.
    """
    path2 = os.path.join(AI_TOOLKIT_DIR, "jobs", "process", "BaseSDTrainProcess.py")
    with open(path2) as f:
        src2 = f.read()
    if "chowchow patch" in src2:
        print("patch_ai_toolkit_for_t4: BaseSDTrainProcess.py already patched, skipping")
        return
    anchor_d = (
        "        unet.to(self.device_torch, dtype=dtype)\n"
        "        unet.requires_grad_(False)\n"
        "        unet.eval()\n"
    )
    replacement_d = (
        "        if self.model_config.quantize:\n"
        "            unet.to(self.device_torch)"
        "  # chowchow patch: skip dtype= cast, it would dequantize the frozen transformer back to bf16\n"
        "        else:\n"
        "            unet.to(self.device_torch, dtype=dtype)\n"
        "        print(f'chowchow patch: after unet.to (quantize={self.model_config.quantize}) "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
        "        unet.requires_grad_(False)\n"
        "        unet.eval()\n"
    )
    assert anchor_d in src2, "patch_ai_toolkit_for_t4: anchor_d not found, upstream file changed"
    src2 = src2.replace(anchor_d, replacement_d, 1)

    # v19's unet.to fix only moved the needle 9.56GB -> 9.72GB (proving that
    # line was NOT the multi-GB spike either) yet the run still OOMed at the
    # exact same 14.5ish GB. The jump must be in LoRA network setup
    # (self.network = NetworkClass(...) / apply_to) or dataset loading
    # (get_dataloader_from_datasets, which triggers cache_latents_to_disk /
    # cache_text_embeddings). Bracket both remaining candidates with memory
    # prints instead of guessing a fourth time.
    anchor_e = (
        "                self.network.apply_to(\n"
        "                    text_encoder,\n"
        "                    unet,"
    )
    replacement_e = (
        "                print(f'chowchow patch: before network.apply_to "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
        "                self.network.apply_to(\n"
        "                    text_encoder,\n"
        "                    unet,"
    )
    assert anchor_e in src2, "patch_ai_toolkit_for_t4: anchor_e not found, upstream file changed"
    src2 = src2.replace(anchor_e, replacement_e, 1)

    anchor_f = (
        "        # load datasets if passed in the root process\n"
        "        if self.datasets is not None:\n"
        "            self.data_loader = get_dataloader_from_datasets(self.datasets, self.train_config.batch_size, self.sd)\n"
        "        if self.datasets_reg is not None:\n"
        "            self.data_loader_reg = get_dataloader_from_datasets(self.datasets_reg, self.train_config.batch_size,\n"
        "                                                                self.sd)\n"
    )
    replacement_f = (
        "        print(f'chowchow patch: before dataloader/caching "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
        # v20: the previous run's log shows this block reaching 9.73GB
        # allocated right before it, then OOMing 80MB short of the 14.56GB T4
        # limit inside cache_text_embeddings()'s first T5 forward pass. Only
        # the VAE (latents) and T5 (text embeddings) are needed for caching --
        # the 12B-param transformer sitting resident on GPU the whole time is
        # pure waste here. Same CPU-swap trick as the T5-quantization patch
        # above, just applied to this window instead.\n"
        "        self.sd.unet.to('cpu')  # chowchow patch: not needed for VAE/T5 caching\n"
        "        flush()\n"
        # v21's crash had the exact same 14.29GB allocated at OOM time as the
        # unpatched v20 run -- the .to('cpu') above may not be freeing what we
        # think it is (accelerate-wrapped module, or the caching allocator
        # just isn't giving it back). Print right here, immediately after the
        # move, instead of assuming it worked.\n"
        "        print(f'chowchow patch: after unet.to(cpu) "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
        "        # load datasets if passed in the root process\n"
        "        if self.datasets is not None:\n"
        "            self.data_loader = get_dataloader_from_datasets(self.datasets, self.train_config.batch_size, self.sd)\n"
        "        if self.datasets_reg is not None:\n"
        "            self.data_loader_reg = get_dataloader_from_datasets(self.datasets_reg, self.train_config.batch_size,\n"
        "                                                                self.sd)\n"
        "        self.sd.unet.to(self.device_torch)  # chowchow patch: bring it back for training\n"
        "        flush()\n"
        "        print(f'chowchow patch: after dataloader/caching "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
    )
    assert anchor_f in src2, "patch_ai_toolkit_for_t4: anchor_f not found, upstream file changed"
    src2 = src2.replace(anchor_f, replacement_f, 1)

    with open(path2, "w") as f:
        f.write(src2)
    print("patched jobs/process/BaseSDTrainProcess.py (skip dtype= cast on quantized unet.to)")


def _patch_dataloader_mixins():
    """v20-v22 all OOM ~80MB short, at the exact same spot, with the exact
    same ~14.29GB allocated -- inside cache_text_embeddings()'s first T5
    forward pass, even with the transformer confirmed offloaded to CPU
    (v22's diagnostic print showed 3.21GB right before this function runs).
    ai-toolkit's own set_device_state_preset('cache_latents') /
    ('cache_text_encoder') machinery already tries to keep unused modules on
    CPU during each caching phase, so the transformer isn't the leak here --
    something inside VAE latent caching (which runs immediately before this
    function, moving the VAE onto GPU) isn't being released before T5's
    memory-hungry per-layer dequantization starts. A flush() at the top of
    cache_text_embeddings() is the cheap thing to try before something more
    invasive; the added print gives real numbers instead of another guess if
    this alone isn't enough.
    """
    path = os.path.join(AI_TOOLKIT_DIR, "toolkit", "dataloader_mixins.py")
    with open(path) as f:
        src = f.read()
    if "chowchow patch" in src:
        print("patch_ai_toolkit_for_t4: dataloader_mixins.py already patched, skipping")
        return

    anchor = (
        "    def cache_text_embeddings(self: 'AiToolkitDataset'):\n"
        "        with accelerator.main_process_first():\n"
        "            print_acc(f\"Caching text_embeddings for {self.dataset_path}\")\n"
    )
    replacement = (
        "    def cache_text_embeddings(self: 'AiToolkitDataset'):\n"
        "        flush()  # chowchow patch: reclaim whatever cache_latents_to_disk left behind\n"
        "        print(f'chowchow patch: at start of cache_text_embeddings "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB')\n"
        "        with accelerator.main_process_first():\n"
        "            print_acc(f\"Caching text_embeddings for {self.dataset_path}\")\n"
    )
    assert anchor in src, "_patch_dataloader_mixins: anchor not found, upstream file changed"
    src = src.replace(anchor, replacement, 1)

    # v23's full traceback + Codex's read of set_device_state_preset pointed at
    # this onload as a possible second contributor (T5 moving CPU->GPU right
    # before the forward pass that OOMs). The @torch.no_grad() fix in
    # _patch_train_tools() is the primary suspect (see point 4 in the module
    # docstring), but this print costs nothing and settles whether T5's
    # quantized buffers were already resident (cheap) or this move itself is
    # unexpectedly large, without guessing a fifth time.
    anchor_g = (
        "                    if not did_move:\n"
        "                        self.sd.set_device_state_preset('cache_text_encoder')\n"
        "                        did_move = True\n"
    )
    replacement_g = (
        "                    if not did_move:\n"
        "                        self.sd.set_device_state_preset('cache_text_encoder')\n"
        "                        did_move = True\n"
        "                        print(f'chowchow patch: after cache_text_encoder preset "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "mem_reserved={torch.cuda.memory_reserved()/1e9:.2f}GB "
        "te1_device={next(self.sd.text_encoder[1].parameters()).device} "
        "te1_requires_grad={any(p.requires_grad for p in self.sd.text_encoder[1].parameters())}')\n"
    )
    assert anchor_g in src, "_patch_dataloader_mixins: anchor_g not found, upstream file changed"
    src = src.replace(anchor_g, replacement_g, 1)

    with open(path, "w") as f:
        f.write(src)
    print("patched toolkit/dataloader_mixins.py (flush + memory prints before/around text-embedding caching)")


def _patch_train_tools():
    """The actual fix (see module docstring point 4): OstrisLinear.forward()
    in uintx_quant.py already wraps its dequantization in torch.no_grad(),
    but the F.linear() matmul that consumes the dequantized weight is NOT
    inside that no_grad block. If the outer call chain isn't under no_grad
    either, autograd builds a graph and MmBackward retains every
    dequantized T5 linear weight for the whole forward pass -- 24 blocks x
    ~386MB (bf16) = ~9.26GB, which matches the observed 3.22GB->14.29GB
    jump almost exactly, and the 80MiB failing allocation matches one
    wi_0 tensor. encode_prompts_flux() (called for both CLIP and T5) is
    never on a path that needs gradients in this run -- caption embeddings
    are cached to disk once and read back during training, and
    train_text_encoder is false -- so wrapping it in torch.no_grad() is
    safe here without touching the quantizer's own generic forward().
    """
    path = os.path.join(AI_TOOLKIT_DIR, "toolkit", "train_tools.py")
    with open(path) as f:
        src = f.read()
    if "chowchow patch" in src:
        print("patch_ai_toolkit_for_t4: train_tools.py already patched, skipping")
        return

    anchor = (
        "def encode_prompts_flux(\n"
        "        tokenizer: List[Union['CLIPTokenizer','T5Tokenizer']],\n"
        "        text_encoder: List[Union['CLIPTextModel', 'T5EncoderModel']],\n"
    )
    replacement = (
        "@torch.no_grad()  # chowchow patch: caching never needs a live autograd\n"
        "# graph through frozen CLIP/T5; without this every dequantized T5 linear\n"
        "# weight is retained for the whole forward (see module docstring point 4)\n"
        "def encode_prompts_flux(\n"
        "        tokenizer: List[Union['CLIPTokenizer','T5Tokenizer']],\n"
        "        text_encoder: List[Union['CLIPTextModel', 'T5EncoderModel']],\n"
    )
    assert anchor in src, "_patch_train_tools: anchor not found, upstream file changed"
    src = src.replace(anchor, replacement, 1)

    anchor_t5_call = (
        "    text_input_ids = text_inputs.input_ids\n"
        "\n"
        "    prompt_embeds = text_encoder[1](text_input_ids.to(device), output_hidden_states=False)[0]\n"
    )
    replacement_t5_call = (
        "    text_input_ids = text_inputs.input_ids\n"
        "\n"
        "    print(f'chowchow patch: before T5 forward "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "te1_requires_grad={any(p.requires_grad for p in text_encoder[1].parameters())} "
        "no_grad_active={not torch.is_grad_enabled()}')\n"
        "    prompt_embeds = text_encoder[1](text_input_ids.to(device), output_hidden_states=False)[0]\n"
        "    print(f'chowchow patch: after T5 forward "
        "mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
        "prompt_embeds_requires_grad={prompt_embeds.requires_grad}')\n"
    )
    assert anchor_t5_call in src, "_patch_train_tools: anchor_t5_call not found, upstream file changed"
    src = src.replace(anchor_t5_call, replacement_t5_call, 1)

    with open(path, "w") as f:
        f.write(src)
    print("patched toolkit/train_tools.py (@torch.no_grad() on encode_prompts_flux + memory prints)")


def get_hf_token():
    token_file = find_dir("/kaggle/input/**/chowchow-lora-hf-token*")
    token_path = os.path.join(token_file, "hf_token.txt")
    assert os.path.isfile(token_path), (
        f"找不到 {token_path}。請確認 kernel-metadata.json 的 dataset_sources "
        "裡有 danielyiyi/chowchow-lora-hf-token，且該 dataset 裡有 hf_token.txt。"
    )
    with open(token_path) as f:
        token = f.read().strip()
    assert token, f"{token_path} 是空的。"
    return token


def main():
    hf_token = get_hf_token()
    os.environ["HF_TOKEN"] = hf_token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = hf_token
    # Both spellings: PyTorch renamed this env var across versions, and the
    # OOM traceback from the first real run named the old one explicitly.
    # Direct assignment, not setdefault -- Kaggle's base image may already
    # set one of these to something else, and a silent no-op here was likely
    # why three straight runs OOMed on the exact same fragmented 28MB gap.
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
    print(f"PYTORCH_CUDA_ALLOC_CONF={os.environ['PYTORCH_CUDA_ALLOC_CONF']!r}")

    dataset_dir_readonly = find_dir("/kaggle/input/**/chowchow-mascot-lora-dataset*")
    # /kaggle/input is read-only; ai-toolkit writes a .aitk_size.json cache
    # file straight into folder_path, so it needs a writable copy.
    dataset_dir = "/kaggle/working/chowchow-mascot-lora-dataset"
    if not os.path.isdir(dataset_dir):
        shutil.copytree(dataset_dir_readonly, dataset_dir)
    print(f"dataset_dir = {dataset_dir} (writable copy of {dataset_dir_readonly})")

    print(f"AI_TOOLKIT_DIR pre-existing = {os.path.isdir(AI_TOOLKIT_DIR)}")
    if not os.path.isdir(AI_TOOLKIT_DIR):
        run(["git", "clone", "https://github.com/ostris/ai-toolkit.git", AI_TOOLKIT_DIR])
    else:
        run(["git", "-C", AI_TOOLKIT_DIR, "log", "-1", "--format=already-cloned at commit %H %cd"])
    patch_ai_toolkit_for_t4()
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=AI_TOOLKIT_DIR)

    with open(CONFIG_PATH, "w") as f:
        f.write(CONFIG_YAML.format(output_name=OUTPUT_NAME, dataset_dir=dataset_dir))

    run([sys.executable, "run.py", CONFIG_PATH], cwd=AI_TOOLKIT_DIR)

    out_dir = f"/kaggle/working/output/{OUTPUT_NAME}"
    safetensors = sorted(
        glob.glob(os.path.join(out_dir, "*.safetensors")),
        key=os.path.getmtime,
    )
    assert safetensors, (
        f"沒有找到訓練輸出的 .safetensors，目錄內容："
        f"{os.listdir(out_dir) if os.path.isdir(out_dir) else 'MISSING'}"
    )
    shutil.copy(safetensors[-1], FINAL_SAFETENSORS)
    print(f"已複製 {safetensors[-1]} -> {FINAL_SAFETENSORS}")


if __name__ == "__main__":
    main()
