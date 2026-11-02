# Musicless

Self-hosted vocal separation for short family audio and videos. Flask serves the application at `/`; a single, isolated GPU worker uses Audio Separator 0.47.0 and an administrator-approved local Mel-Band RoFormer Big Beta 4 checkpoint. No transcription, cloud inference, model catalog, or model downloads.

## Status

Development and the automated suite run on Windows/WSL2 with Python 3.12.8 and real FFmpeg/FFprobe. Tests cover authentication, CSRF, malicious model selections, local file integrity, queue concurrency, ownership, expiry, cleanup, all nine container round trips, a 65-second video, and delayed audio alignment. Media tests substitute extracted lossless audio for the vocal stem; they do **not** demonstrate GPU inference quality.

Latest local result: **50 passed, 2 skipped** (the skips are real GPU inference and the Docker network-namespace check on a host without Docker), using FFmpeg/FFprobe 9.0.2. `node --check musicless/static/app.js` and Python compilation checks passed.

## Tested on

| Component | Version |
|---|---|
| GPU | NVIDIA GeForce GTX 1060 6 GB |
| NVIDIA driver | 580.178.04 |
| CUDA runtime | 12.6 (`+cu126` PyTorch wheels) |
| PyTorch / Torchaudio / Torchvision | 2.11.0+cu126 / 2.11.0+cu126 / 0.26.0+cu126 |
| Python | 3.12.8 |
| Base image | `python:3.12.8-slim-bookworm` |
| Audio Separator | 0.47.0 |
| Model | Mel-Band RoFormer Big Beta 4 (1.57 GB checkpoint) |

The reference deployment is one Docker host with a single GPU passed through to the worker. Inside the worker `torch.cuda.is_available()` reports `True`, the device is the GTX 1060 6 GB with roughly 6.1 GB total VRAM, and the enrolled checkpoint passes the initial integrity check. End-to-end job timing on this card has not been benchmarked; the default 900-second job deadline is the guard that matters. Larger GPUs give more headroom for the 1.57 GB model. A detected GPU is not proof of successful inference — run the GPU acceptance test below.

The Docker build runs `pip check`, imports Audio Separator, and asserts the exact Torch, Torchaudio and CUDA build versions. Do not treat dependency resolution as a successful GPU test.

## First start: Windows with WSL2 / Linux

Use Docker Desktop's Linux engine with WSL2 GPU support, or Docker Engine in your Linux/WSL environment. On Linux, install/configure the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) on the host. No container installs GPU drivers. A VM-based deployment also needs functioning GPU passthrough and its own driver/toolkit. Use a local Linux filesystem for the SQLite data directory, **not SMB/NFS or `/mnt/c`/`/mnt/d`**. The source and read-only model directory may reside on a Windows mount.

Run these commands in a WSL/Linux shell from the project directory:

```bash
docker build --target web -t musicless-web .
cp .env.example .env
mkdir -p models
mkdir -p "$HOME/musicless-data"
sudo chown 10001:10001 "$HOME/musicless-data"
docker run --rm -it --network none musicless-web python -m musicless.manage credentials
```

Edit `.env`: paste the two generated values, surround `ADMIN_PASSWORD_HASH` with **single quotes** (its `$` characters must remain literal), and set `DATA_DIR` to the absolute result of `echo "$HOME/musicless-data"`. Leave `COOKIE_SECURE=false` and `BIND_ADDRESS=127.0.0.1` for local HTTP. Use a strong password. Credentials are generated locally; the application never prints them. The setup command intentionally prints the hash and random session secret for you to place in `.env`; do not share that output. Keep `.env` private (`chmod 600 .env`).

Manually place your **existing** files under `models/`:

```text
models/melband_roformer_big_beta4.ckpt
models/config_melbandroformer_big_beta4.yaml
```

Alternatively, set `MODELS_DIR` to the existing directory containing both files; this avoids copying or moving the 1.57 GB checkpoint. Do not change filenames or enroll an untrusted checkpoint. Supply the exact YAML from your known working local Audio Separator cache. Missing YAML is an error; the app will not fetch it. The mapping was verified against the installed 0.47.0 wheel's `audio_separator/models.json` and [version-specific source](https://github.com/nomadkaraoke/python-audio-separator/blob/v0.47.0/audio_separator/models.json).

Record checksums explicitly, with only the registry writable. Substitute your actual model directory if using an external one:

```bash
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$PWD/models:/models:ro" -v "$PWD/config:/config" \
  musicless-web python -m musicless.manage enroll big-beta-4 \
  --models /models --registry /config/models.json
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=80 worker
```

Open **http://localhost:8000** and wait for the worker's initial model integrity check. Visitors can upload and download without signing in. Use **Admin sign in** to access `/settings`, which shows CUDA/GPU/free VRAM, file availability, default model, and optional model choice. All model files must be readable by UID 10001. Initial checks hash and CRC-check the full checkpoint and may take time on Windows-backed storage.

Quick GPU check using the built image, with networking still disabled:

```bash
docker compose run --rm worker python -c 'import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0)); print(torch.cuda.mem_get_info())'
```

The [official PyTorch version matrix](https://pytorch.org/get-started/previous-versions/) lists Torch/Torchaudio 2.11.0 with CUDA 12.6. We use matching `+cu126` wheels (including Torchvision 0.26.0), Python 3.12.8 on Debian Bookworm, and the wheels' CUDA userspace libraries, rather than overlaying a separate CUDA image. GPU reservations follow [Docker's documented Compose syntax](https://docs.docker.com/compose/how-tos/gpu-support/). ONNX Runtime is CPU-only because this approved model uses PyTorch CUDA; its ONNX acceleration warning does not mean RoFormer runs on CPU.

## Formats and predictable media handling

| Extension | Actual accepted container | Output audio | Video policy |
|---|---|---|---|
| `.mp4` | ISO BMFF/MP4, not QuickTime | AAC | Copy H.264, HEVC, AV1, MPEG-4 |
| `.mov` | QuickTime with `qt` major brand | AAC | Copy H.264, HEVC, ProRes, MPEG-4 |
| `.mkv` | Matroska | AAC | Copy H.264, HEVC, AV1, VP8/9, MPEG-4/2, ProRes |
| `.webm` | WebM DocType | Opus | Copy VP8/9, AV1 |
| `.mp3` | MP3 | MP3 | Audio only |
| `.wav` | WAV | PCM 16-bit | Audio only |
| `.flac` | FLAC | FLAC | Audio only |
| `.m4a` | ISO BMFF audio | AAC | Audio only |
| `.ogg` | Ogg audio | Vorbis | Audio only |

Input audio codecs: AAC, MP3, Opus, Vorbis, FLAC, ALAC, AC3/EAC3, and listed PCM integer/float variants. Only mono/stereo 8–96 kHz is accepted. Video resolution is limited to 3840×2160 pixels. Extensions are normalized to lowercase. Container-family aliases (e.g. audio-only MP4 versus M4A) are accepted where structurally compatible; renaming WAV to MP3 is rejected.

Exactly **one audio track**, at most **one video track**, and **no subtitle, attachment, cover-art or data streams** are accepted. Export a simpler file first if rejected. This avoids silently dropping alternate language tracks or subtitles. Arbitrary tags and chapters are deliberately stripped for predictable/privacy-conscious output; video stream rotation/display-matrix information travels with the stream copy. VFR timestamps are copied, not converted to a constant frame rate. Negative/unusable audio offsets are rejected. Extraction aligns audio to the normalized input timeline, pads gaps/trailing silence, and includes the full declared media duration. The final file is probed and completely decoded before release, and duration must match within 250 ms (codec padding tolerance). There is no `-shortest`, preview-only duration, or 20/60-second cut. Unusual timestamp/rotation combinations still need acceptance testing with representative files.

Lossless 44.1 kHz stereo float WAV is used before separation. The adapter requests only Vocals and validates the path returned by Audio Separator. Final audio is genuinely encoded in the appropriate codec. A video is never unnecessarily re-encoded. Arabic speech and singing receive the model's normal vocal separation; fidelity is model-dependent, with no language-specific processing.

## Offline enforcement and adding models

The worker has `network_mode: none`, no ports, no Docker socket, a read-only root filesystem and read-only `/models` and `/config`. GPU device access does not require a network. The web has neither GPUs, Audio Separator nor a model mount. Both run as UID 10001 with capabilities dropped. A shared SQLite database and UUID job directories on a local volume are their only queue transport.

The web resolves an internal ID from `config/models.json`, requests a fresh filesystem preflight from the worker **before reading upload bytes**, and rejects unknown/unsupported selection syntax. The worker rechecks before processing and before loading. SHA-256 checks, minimum checkpoint size, PyTorch ZIP integrity/CRC, regular-file/no-symlink containment and safe YAML structure checks detect missing, truncated or changed files. Checksums establish identity relative to your explicit enrollment; they do not prove that an untrusted downloaded checkpoint is safe. Only enroll your trusted models.

The adapter overrides Audio Separator's catalog/download resolver with a fixed local MDXC checkpoint/config pair. It disables catalog, downloader, and hash-to-online-metadata methods. A Python socket guard fails closed on network calls. Docker's network namespace is the stronger boundary, including calls made by native libraries/subprocesses. Environment offline flags are additional hints only. No runtime installer, URL import, arbitrary path, CLI-option, retry-to-download, or model registration endpoint exists.

To add a model: stop the worker, manually obtain trusted files outside the app, add an entry to the host registry with unique lowercase ID, name, simple `.ckpt` and `.yaml` filenames, sensible `min_bytes`, and null hashes. Run the same explicit `enroll` command for its ID, restart services, run the optional offline GPU test adapted to that ID, then select it in settings. The initial adapter supports **MDXC/RoFormer only**; a different architecture requires a reviewed local adapter and tests. Do not add an online catalog to this registry. Registry files are administrator-edited on the host, never through user requests.

## Limits, queue and recovery

Defaults: 250 MiB input, 120 seconds media, one processing job, three uploading/queued jobs, one outstanding job per visitor session or administrator account, 900-second processing deadline, one-hour successful-output retention, 2 GiB free-space floor and 1.5 GiB per-job storage budget. All are in `.env`; processing concurrency is deliberately fixed at one. Visitors receive a private browser session and can upload, view, cancel, and download only their own jobs. Keep the same browser session to retrieve results; clearing cookies loses access. The administrator login is only needed for settings. There is no public registration. Guest write throttling uses the immediate peer address (shared behind a proxy); queue and storage limits also apply globally.

Uploads are raw streamed HTTP bodies, not eagerly buffered multipart forms. The API accepts only `filename` and `model_id` query arguments, with an `X-CSRF-Token` header. Queue slots/storage reservations use `BEGIN IMMEDIATE` transactions. Limits count in-progress uploads. FFprobe/FFmpeg run only in the bounded worker, with fixed argument arrays and `shell=False`. File validation errors appear asynchronously after the upload is received. The supervisor enforces timeout/cancellation/storage bounds, kills the entire process group, and releases failed jobs. Successful intermediates are removed immediately; failures/cancellations are cleaned on the next supervisor pass. Stale uploads expire after 20 minutes without progress, stale queued jobs after one hour, and old terminal metadata after one day. Active processing files are excluded from cleanup. The worker's exclusive OS lock prevents a second worker; SQLite claims also prevent duplicate execution.

Worker restart marks interrupted processing jobs failed; they are not silently retried. `tini -g` forwards termination to children and container teardown kills all remaining processes. Ordinary web restarts preserve the queue. Run only one worker container for the data volume. Do not start a separate worker against a volume while manually killing just the supervisor process inside another live container.

SQLite and workspace storage require actual capacity; the floor and budget are admission/runtime limits rather than filesystem quotas. A burst can overshoot the per-job budget between supervisor checks, but only one processing child exists and container memory/process limits remain enforced. Use a bounded host filesystem for hard disk quotas if required. A stopped worker cannot clean files until it restarts.

## Reverse proxy and HTTPS

No proxy configuration is changed automatically. First confirm local processing, then manually configure your HTTPS proxy host to forward HTTP to port 8000 on the Musicless web container/private host. Set `COOKIE_SECURE=true` before HTTPS use. The app does not trust `X-Forwarded-*` identity headers and has no `ProxyFix`; secure-cookie behavior is explicitly configured, redirects are relative, and no forwarded host is used to construct URLs. Thus there is no broad trusted-proxy range to configure. Login throttling uses the immediate peer address, so behind a proxy it conservatively shares the login rate limit across clients. Your proxy can additionally rate-limit by the real client IP.

Suggested nginx settings (apply in the appropriate server/location context — Nginx Proxy Manager, NPMplus, Caddy and Traefik all work as long as upstream stays HTTP/1.1):

```nginx
client_max_body_size 250m;
client_body_timeout 300s;
proxy_connect_timeout 10s;
proxy_send_timeout 300s;
proxy_read_timeout 300s;
proxy_request_buffering off;
proxy_buffering off;
```

Uploads need time for local model preflight plus file transfer; processing uses polling, so the proxy need not hold a connection for the 15-minute job. Set equivalent header/body/connection rate controls at the proxy. Keep HTTP/1.1 upstream and standard Host forwarding. With buffering enabled the proxy can store the full upload before the application sees it; disabling it preserves application backpressure. Secure cookies, HTTP-only cookies, SameSite Strict, signed eight-hour sessions, CSRF validation, CSP and ownership checks are implemented. Change `SESSION_SECRET` to invalidate existing sessions after a password change.

Keep the default loopback port binding for local-only access. If your reverse proxy runs in another container, connect **web only** to a private proxy network via a local Compose override and remove published ports, or bind to a private host address/firewall that admits only your proxy. Loopback inside a proxy container is not the Docker host. Never publish port 8000 unrestricted to the Internet; do not attach the worker to the proxy network. Restrict direct access while keeping the public upload page accessible through the proxy.

## Tests and hardware acceptance

No existing personal media or checkpoint is inspected by the ordinary test suite. Fixtures are synthetic and generated in temporary directories.

```bash
docker build --target test -t musicless-test .
docker run --rm --network none -e ASSERT_NETWORK_NONE=1 musicless-test
```

This runs adversarial tests with the actual container network disabled. For local development install Python 3.12, FFmpeg/FFprobe, then `python -m pip install -r requirements-test.txt` and `python -m pytest -q`. Set `FFMPEG`/`FFPROBE` to binary paths if absent from PATH. The checked-in lock files pin the Linux application dependency sets; worker runtime validation still requires the Docker build.

Optional **real GPU** acceptance (build-time dependency installation has internet access, runtime has none):

```bash
docker build --target gpu-test -t musicless-gpu-test .
docker run --rm --gpus all --network none --read-only \
  --tmpfs /tmp:rw,size=1g,mode=1777 \
  -e DATA_DIR=/tmp/data -e MODEL_REGISTRY=/config/models.json -e MODELS_DIR=/models \
  -e RUN_GPU_TESTS=1 -e ASSERT_NETWORK_NONE=1 \
  -v "$PWD/config:/config:ro" -v "$PWD/models:/models:ro" \
  musicless-gpu-test python -m pytest -q -m 'gpu or container' -p no:cacheprovider
```

Use the actual external model path if applicable. The GPU test requires CUDA, performs real synthetic-video separation/remux/verification, checks unchanged model file size/mtime, and asserts no Python network operation was attempted. Run the normal offline suite too: it verifies missing/corrupt models fail without modifying files. Inspect runtime isolation with `docker inspect "$(docker compose ps -q worker)"` (NetworkMode `none`; `/models` RW false). Do not paste `docker compose config` or inspection of the web environment publicly: it can contain secrets.

Then process representative, non-sensitive 120-second audio/video through the UI on your target GPU, measure peak VRAM (`nvidia-smi` on the host), check lip sync and orientation, cancel a job, restart the worker mid-job, and verify one-hour expiry. Repeat behind your reverse proxy. No lower-quality settings or alternative models have been tested; none are silently selected. `CUDA_OOM` is a visible failure. If the checkpoint does not fit your card, the options are a larger GPU, a different approved local model, or CPU mode with `docker compose -f compose.yaml -f compose.cpu.yaml up -d` (modern Compose with `!reset` support; CPU may exceed 15 minutes).

## Moving to another host, and troubleshooting

Transfer only project source and configuration to the new host, with your existing model files transferred separately by your chosen trusted method. Do not copy `.venv`, `.python`, `.uv-cache`, local scratch directories, uploaded media or databases as source. Stop services before backing up/restoring `data`; preserve UID 10001 ownership and use local storage. Create a fresh `.env` with appropriate absolute paths, recheck model hashes, build, run offline/GPU tests, and start Compose.

* **MODEL_NOT_AVAILABLE:** exact checkpoint and YAML filenames, regular files/no symlinks, permissions, minimum size, enrolled hashes and correct YAML structure. Re-enroll only after intentionally verifying replacement files.
* **Worker offline/starting:** check `docker compose ps` and worker logs; initial full-file checks may take time. Confirm writable data directory and SQLite local storage.
* **CUDA_UNAVAILABLE / CUDA_OOM:** verify host driver/toolkit/passthrough, GPU diagnostic command, available VRAM and optional GPU test. No automatic CPU fallback.
* **PROCESSING_FAILED / MEDIA_FAILED:** unsupported/corrupt media or incompatible model/dependencies. Use the synthetic tests to distinguish installation from source-media issues. Raw FFmpeg/Python errors are deliberately not exposed to browsers.
* **QUEUE_FULL / USER_LIMIT:** wait or cancel the outstanding job. Limits apply per visitor session or administrator account.
* **DISK_SPACE / DISK_LIMIT:** free space or deliberately adjust floor/budget after checking capacity.
* **413 / stalled uploads:** align proxy/app limits and timeouts, disable proxy request buffering, check local model preflight speed.
* **Login loops:** `COOKIE_SECURE=false` is required for plain local HTTP; true is required behind public HTTPS. Preserve literal `$` characters in the hash.

The app is intentionally small and conservative. There is no automatic updater, model fetcher, user management console, or silent quality downgrade.

## Interface languages and data-handling notice

The interface defaults to Arabic with right-to-left layout. The ENG / ARA control is available on every page and stores the preference in the session cookie, including across administrator sign-in and sign-out. Templates, processing status, errors, and settings use the shared Arabic catalogue in `musicless/translations.json`.

The public `/privacy` page explains server-side processing (not browser-side), stored media and job metadata, configured output retention, cleanup timing and limitations, session cookies, and the limits of application-managed deletion. It is linked from every page and available in both languages. Its retention and timeout values come from the web configuration; keep these settings consistent between web and worker processes when deploying them separately. Infrastructure log and backup retention remain the operator’s responsibility.

## Source layout

- `musicless/web.py`: Flask app, HTTP routes, and streamed uploads (`POST /api/jobs`).
- `musicless/static/app.js`: browser uploads, progress, and job polling.
- `musicless/worker.py`: queued job processing and worker supervision.
- `musicless/media.py`: media validation and FFmpeg operations.
- `musicless/db.py`: SQLite storage and queue management.
- `config/models.json`: approved model registry; enroll your trusted local files before use.
- `tests/`: automated tests using synthetic fixtures.

## Preparing a source commit

Keep `.env`, model weights and their companion YAML, media, runtime data, and local
scratch downloads outside Git. The ignore rules exclude these local artifacts;
`.env.example` is the public configuration template. Docker uses a separate build
allowlist in `.dockerignore`.

Before committing, review `git status --short` and `git diff --cached`. Do not use
`git add -f` to include ignored credentials or model files. Model files must be
supplied separately by each operator.

This project is released under the GNU General Public License v2.0 — see `LICENSE`.
Third-party dependencies and model files keep their own licenses and terms; the
Mel-Band RoFormer checkpoint is not distributed here.
