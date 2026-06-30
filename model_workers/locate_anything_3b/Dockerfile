FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/opt/hf
ENV TORCH_HOME=/opt/torch

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip \
    && pip install torch==2.11.0 torchvision==0.26.0 \
        --index-url https://download.pytorch.org/whl/cu128

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

RUN mkdir -p /opt/venv/lib/python3.12/site-packages/decord \
    && touch /opt/venv/lib/python3.12/site-packages/decord/__init__.py

RUN pip install "nvidia-cuda-nvrtc-cu12==12.9.86"

RUN python3 -c "import torch, torchvision; print('torch', torch.__version__, 'tv', torchvision.__version__); import torchvision.ops; print('nms_ok')"

RUN python3 -c "import ctypes, glob; lib=ctypes.CDLL(sorted(glob.glob('/opt/venv/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib/libnvrtc.so*'))[0]); n=ctypes.c_int(); lib.nvrtcGetNumSupportedArchs(ctypes.byref(n)); arr=(ctypes.c_int*n.value)(); lib.nvrtcGetSupportedArchs(arr); assert 121 in list(arr), 'nvrtc lacks sm_121'; print('nvrtc_sm121_ok')"

COPY server.py /app/server.py
WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
