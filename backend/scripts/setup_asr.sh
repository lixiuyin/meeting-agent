#!/usr/bin/env bash
set -euo pipefail

# Launch docker
# NVIDIA PyTorch Container 24.07 ~ 25.12 verified.
# Previous versions are also compatible.
sudo docker run --privileged --net=host --ipc=host --ulimit memlock=-1:-1 --ulimit stack=-1:-1 --gpus all --rm -it  nvcr.io/nvidia/pytorch:25.12-py3
## If flash attention is not included in your docker environment, you need to install it manually
## Refer to https://github.com/Dao-AILab/flash-attention for installation instructions
# pip install flash-attn --no-build-isolation

# Install from github
git clone https://github.com/microsoft/VibeVoice.git
cd VibeVoice || exit 1
pip install -e .

# Usage 1: Launch Gradio demo
apt update && apt install ffmpeg -y # for demo
python demo/vibevoice_asr_gradio_demo.py --model_path microsoft/VibeVoice-ASR --share

# Usage 2: Inference from files directly
python demo/vibevoice_asr_inference_from_file.py --model_path microsoft/VibeVoice-ASR --audio_files asr/files/meeting.mp4
