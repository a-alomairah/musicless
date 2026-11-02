# PyTorch's cu126 wheels supply CUDA userspace; no arbitrary CUDA base layering.
FROM python:3.12.8-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 tini && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-web.txt requirements-web.lock ./
RUN pip install -r requirements-web.lock
RUN groupadd -g 10001 musicless && useradd -u 10001 -g musicless -M musicless && mkdir /data && chown 10001:10001 /data
COPY musicless ./musicless
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]

FROM base AS web
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "musicless.web:create_app()", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "300", "--access-logfile", "/dev/null"]

FROM base AS worker
COPY requirements-worker.txt constraints-worker.txt requirements-worker.lock ./
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
RUN pip install torch==2.11.0+cu126 torchaudio==2.11.0+cu126 torchvision==0.26.0+cu126 --index-url https://download.pytorch.org/whl/cu126
RUN pip install -r requirements-worker.lock --extra-index-url https://download.pytorch.org/whl/cu126 && pip check
RUN python -c "import torch, torchaudio; from audio_separator.separator import Separator; from importlib.metadata import version; assert torch.__version__ == '2.11.0+cu126'; assert torchaudio.__version__ == '2.11.0+cu126'; assert torch.version.cuda == '12.6'; assert version('audio-separator') == '0.47.0'"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TORCH_HOME=/tmp/torch HOME=/tmp OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 NUMBA_CACHE_DIR=/tmp/numba XDG_CACHE_HOME=/tmp/cache
USER 10001:10001
CMD ["python", "-m", "musicless.worker"]

FROM worker AS gpu-test
USER root
RUN pip install pytest==8.4.2
COPY tests ./tests
COPY pytest.ini .
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "-m", "gpu", "-p", "no:cacheprovider"]

FROM base AS test
COPY requirements-test.txt .
RUN pip install -r requirements-test.txt
COPY tests ./tests
COPY config ./config
COPY pytest.ini .
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
