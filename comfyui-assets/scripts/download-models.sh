#!/bin/bash
# ComfyUI Cafe Loop Generator - Commercial Model Downloader
# Downloads the commercial Apache 2.0 model stack for the WAN 2.2 FLF2V workflow.
#
# Usage: bash download-models.sh
# Default target: /workspace/ComfyUI (RunPod/Vast.ai standard)
# Override: MODEL_DIR="/your/path/ComfyUI" bash download-models.sh
# The FLUX.1 Schnell keyframe checkpoint is downloaded by default.
# Set DOWNLOAD_KEYFRAME=0 to skip that optional 17.2 GB download.
# Set DOWNLOAD_MPS_KEYFRAME=1 to also download the GGUF keyframe stack used
# by workflows/cafe-keyframe-flux-mps.json on Apple Silicon / MPS (~9 GB).
# Set DOWNLOAD_MPS_1080P=1 to download the optimised Apple Silicon stack used by
# workflows/cafe-flf2v-wan22-mps.json (generation) and
# workflows/cafe-upscale-1080p-mps.json (post-process upscale) (~30 GB):
# higher-quality Q5_K_M WAN weights, a Q8_0 text encoder, the Lightning
# 4-step LoRAs, RIFE frame interpolation and an ESRGAN upscaler.
# All MPS workflows require the city96/ComfyUI-GGUF custom node.

set -euo pipefail

COMFYUI_BASE="${MODEL_DIR:-/workspace/ComfyUI}"
DOWNLOAD_KEYFRAME="${DOWNLOAD_KEYFRAME:-1}"
DOWNLOAD_MPS_KEYFRAME="${DOWNLOAD_MPS_KEYFRAME:-0}"
DOWNLOAD_MPS_1080P="${DOWNLOAD_MPS_1080P:-0}"

echo "ComfyUI Cafe Loop Generator - Commercial Model Downloader"
echo "   Target: ${COMFYUI_BASE}"
echo ""

mkdir -p "${COMFYUI_BASE}/models/diffusion_models"
mkdir -p "${COMFYUI_BASE}/models/text_encoders"
mkdir -p "${COMFYUI_BASE}/models/vae"

download_model() {
  local label="$1"
  local destination="$2"
  local url="$3"
  local partial="${destination}.part"

  echo "Downloading ${label}..."
  if [ -s "${destination}" ]; then
    echo "   Already exists, skipping."
    return
  fi

  # Keep partial downloads so a later run can resume instead of starting over.
  curl --fail --location --show-error \
    --retry 5 --retry-delay 5 --retry-all-errors \
    --continue-at - \
    --output "${partial}" \
    "${url}"
  mv "${partial}" "${destination}"
}

# --- WAN 2.2 diffusion models ---
download_model \
  "WAN 2.2 High Noise Model (fp8)" \
  "${COMFYUI_BASE}/models/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors" \
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"

download_model \
  "WAN 2.2 Low Noise Model (fp8)" \
  "${COMFYUI_BASE}/models/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors" \
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"

# --- WAN text encoder and VAE ---
download_model \
  "UMT5 XXL Text Encoder (fp8)" \
  "${COMFYUI_BASE}/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"

download_model \
  "WAN 2.1 VAE" \
  "${COMFYUI_BASE}/models/vae/wan_2.1_vae.safetensors" \
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors"

# --- Commercial FLUX.1 Schnell keyframe model ---
if [ "${DOWNLOAD_KEYFRAME}" = "1" ]; then
  mkdir -p "${COMFYUI_BASE}/models/checkpoints"
  download_model \
    "FLUX.1 Schnell checkpoint (fp8)" \
    "${COMFYUI_BASE}/models/checkpoints/flux1-schnell-fp8.safetensors" \
    "https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors"
else
  echo "Skipping FLUX.1 Schnell keyframe checkpoint."
  echo "   To enable it: DOWNLOAD_KEYFRAME=1 bash download-models.sh"
fi

# --- Optional Mac / Apple Silicon (MPS) GGUF keyframe stack ---
if [ "${DOWNLOAD_MPS_KEYFRAME}" = "1" ]; then
  mkdir -p "${COMFYUI_BASE}/models/unet"
  mkdir -p "${COMFYUI_BASE}/models/clip"
  mkdir -p "${COMFYUI_BASE}/models/vae"

  download_model \
    "FLUX.1 Schnell GGUF Q4_K_S (MPS keyframe)" \
    "${COMFYUI_BASE}/models/unet/flux1-schnell-Q4_K_S.gguf" \
    "https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q4_K_S.gguf"

  download_model \
    "UMT5 XXL Text Encoder GGUF Q4_K_S (MPS keyframe)" \
    "${COMFYUI_BASE}/models/clip/t5-v1_1-xxl-encoder-Q4_K_S.gguf" \
    "https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf/resolve/main/t5-v1_1-xxl-encoder-Q4_K_S.gguf"

  download_model \
    "CLIP L Text Encoder (MPS keyframe)" \
    "${COMFYUI_BASE}/models/clip/clip_l.safetensors" \
    "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors"

  download_model \
    "FLUX VAE (MPS keyframe)" \
    "${COMFYUI_BASE}/models/vae/ae.safetensors" \
    "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"

  echo "   Note: workflows/cafe-keyframe-flux-mps.json requires the"
  echo "   city96/ComfyUI-GGUF custom node (UnetLoaderGGUF, DualCLIPLoaderGGUF)."
else
  echo "Skipping Mac / Apple Silicon (MPS) GGUF keyframe stack."
  echo "   To enable it: DOWNLOAD_MPS_KEYFRAME=1 bash download-models.sh"
fi

# The old DOWNLOAD_MPS_ANIMATION stack (WAN 2.2 Q3_K_S + UMT5 Q3_K_S, ~16 GB) was
# removed. DOWNLOAD_MPS_1080P below supersedes it with Q5_K_M / Q8_0 weights, and
# keeping the Q3 option around only invited re-downloading 16 GB of strictly worse
# weights that no remaining workflow references.

# --- Optional Apple Silicon (MPS) optimised 1080p stack ---
# Rationale for each file is documented in README.md "步驟 0".
if [ "${DOWNLOAD_MPS_1080P}" = "1" ]; then
  mkdir -p "${COMFYUI_BASE}/models/unet"
  mkdir -p "${COMFYUI_BASE}/models/clip"
  mkdir -p "${COMFYUI_BASE}/models/vae"
  mkdir -p "${COMFYUI_BASE}/models/loras"
  mkdir -p "${COMFYUI_BASE}/models/frame_interpolation"
  mkdir -p "${COMFYUI_BASE}/models/upscale_models"

  # Q5_K_M instead of Q3_K_S: Q3 visibly degrades material and lighting detail,
  # which is exactly what a photoreal cafe scene depends on. Only one expert is
  # resident at a time, so 10.8 GB each is comfortable on a 64 GB machine.
  download_model \
    "WAN 2.2 High Noise GGUF Q5_K_M (MPS 1080p)" \
    "${COMFYUI_BASE}/models/unet/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf" \
    "https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF/resolve/main/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"

  download_model \
    "WAN 2.2 Low Noise GGUF Q5_K_M (MPS 1080p)" \
    "${COMFYUI_BASE}/models/unet/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf" \
    "https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF/resolve/main/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf"

  # Prompt adherence lives in the text encoder. Q3_K_S reads the prompt through a
  # heavily lossy encoder; Q8_0 costs 3 GB more and runs once per prompt change.
  download_model \
    "UMT5 XXL Text Encoder GGUF Q8_0 (MPS 1080p)" \
    "${COMFYUI_BASE}/models/clip/umt5-xxl-encoder-Q8_0.gguf" \
    "https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q8_0.gguf"

  download_model \
    "WAN 2.1 VAE (MPS 1080p)" \
    "${COMFYUI_BASE}/models/vae/wan_2.1_vae.safetensors" \
    "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors"

  # Lightning 4-step distillation: 20 steps at CFG 3.5 (40 model evaluations)
  # collapses to 4 steps at CFG 1.0 (4 evaluations). This is the single largest
  # speedup available and it is what makes an 8-second loop practical here.
  download_model \
    "WAN 2.2 Lightning 4-step LoRA — High Noise (MPS 1080p)" \
    "${COMFYUI_BASE}/models/loras/wan2.2_i2v_A14B_high_noise_lightning_4step.safetensors" \
    "https://huggingface.co/lightx2v/Wan2.2-Lightning/resolve/main/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/high_noise_model.safetensors"

  download_model \
    "WAN 2.2 Lightning 4-step LoRA — Low Noise (MPS 1080p)" \
    "${COMFYUI_BASE}/models/loras/wan2.2_i2v_A14B_low_noise_lightning_4step.safetensors" \
    "https://huggingface.co/lightx2v/Wan2.2-Lightning/resolve/main/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/low_noise_model.safetensors"

  # RIFE 4.26 for 2x frame interpolation. Diffusion cost grows roughly with the
  # square of the token count, so generating 65 frames and interpolating to 129
  # is about 3x cheaper than generating 129 frames outright.
  download_model \
    "RIFE v4.26 frame interpolation (MPS 1080p)" \
    "${COMFYUI_BASE}/models/frame_interpolation/rife_v4.26.safetensors" \
    "https://huggingface.co/Comfy-Org/frame_interpolation/resolve/main/frame_interpolation/rife_v4.26.safetensors"

  # Real-ESRGAN x4plus is BSD-3-Clause, i.e. usable commercially. The popular
  # community upscalers 4x_foolhardy_Remacri and 4x-UltraSharp look sharper but
  # are both CC-BY-NC-SA-4.0 (non-commercial) and must not be used here.
  # It is also photo-trained and comparatively soft, which suits video: an
  # aggressive sharpener reads as per-frame flicker once the frames are played back.
  download_model \
    "Real-ESRGAN x4plus upscaler (MPS 1080p)" \
    "${COMFYUI_BASE}/models/upscale_models/RealESRGAN_x4plus.pth" \
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"

  echo "   Note: workflows/cafe-flf2v-wan22-mps.json requires the"
  echo "   city96/ComfyUI-GGUF custom node (UnetLoaderGGUF, CLIPLoaderGGUF)."
  echo "   Frame interpolation (generation) and upscaling (cafe-upscale-1080p-mps.json)"
  echo "   both use built-in ComfyUI nodes."
else
  echo "Skipping Apple Silicon (MPS) optimised 1080p stack."
  echo "   To enable it: DOWNLOAD_MPS_1080P=1 bash download-models.sh"
fi

echo ""
echo "Commercial model stack downloaded to ${COMFYUI_BASE}/"
echo "Next steps (Apple Silicon / MPS — the only pipeline this repo ships today):"
echo "  1. Open ComfyUI at http://localhost:8188"
echo "  2. Load workflows/cafe-keyframe-flux-mps.json for a keyframe"
echo "  3. Load workflows/cafe-flf2v-wan22-mps.json, set node 9 to 768x432 for"
echo "     a cheap motion test-render (or 1024x576 for the official generation)"
echo "  4. Load workflows/cafe-upscale-1080p-mps.json to upscale an approved"
echo "     clip to 1920x1080 -- this is post-process only, motion is unchanged"
