"""Cinematic Montage Service — прод-сервис кинематографичного редактирования фото.

POST /api/jobs      — загрузить фото + конфиг, получить job_id
GET  /api/jobs/{id} — статус и список готовых картинок
GET  /api/jobs/{id}/images/{name} — скачать картинку
GET  /api/styles    — доступные пресеты
GET  /api/models    — доступные нейросети
GET  /healthz       — liveness

Auth: SERVICE_TOKEN обязателен (fail-closed); все /api/* требуют
Authorization: Bearer <token>. Для локальной разработки — ALLOW_NO_AUTH=1.
"""
import hmac
import json
import logging
import os
import pathlib
import re
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from providers import DEFAULT_MODEL_ID, MODELS, PROVIDERS, ProviderError
from styles import STYLES, build_prompt

log = logging.getLogger("montage")
logging.basicConfig(level=logging.INFO)

DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR", "./data")).resolve()
WORKERS = int(os.environ.get("WORKERS", "4"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "15"))
MAX_TASKS_PER_JOB = int(os.environ.get("MAX_TASKS_PER_JOB", "24"))
MAX_PENDING_TASKS = int(os.environ.get("MAX_PENDING_TASKS", "96"))
MAX_JOBS_IN_MEMORY = int(os.environ.get("MAX_JOBS_IN_MEMORY", "512"))
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")

if not SERVICE_TOKEN and os.environ.get("ALLOW_NO_AUTH") != "1":
    raise RuntimeError(
        "SERVICE_TOKEN is not set. Set it (openssl rand -hex 32), "
        "or run with ALLOW_NO_AUTH=1 explicitly for local development.")

ALLOWED_ASPECTS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"}
ALLOWED_SIZES = {"1K", "2K", "4K"}
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
SAFE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z")

DATA_DIR.mkdir(parents=True, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=WORKERS)
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
pending_tasks = 0
pending_lock = threading.Lock()


def _atomic_write_bytes(path: pathlib.Path, data: bytes):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _job_dir(job_id: str) -> pathlib.Path:
    path = (DATA_DIR / job_id).resolve()
    if not path.is_relative_to(DATA_DIR):
        raise HTTPException(404, "not found")
    return path


def _save_manifest(job: dict):
    snapshot = {k: v for k, v in job.items() if k != "lock"}
    _atomic_write_bytes(_job_dir(job["id"]) / "manifest.json",
                        json.dumps(snapshot, ensure_ascii=False, indent=1).encode())


def _mark_interrupted(job: dict) -> dict:
    job["status"] = "partial" if job.get("images") else "failed"
    job.setdefault("errors", []).append(
        {"task": "*", "error": "interrupted by service restart"})
    return job


@asynccontextmanager
async def lifespan(_app):
    # Рестарт-реконсиляция: очередь задач не переживает перезапуск,
    # поэтому все "running" из манифестов переводим в терминальное состояние.
    for manifest in DATA_DIR.glob("*/manifest.json"):
        try:
            job = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            job = {"id": manifest.parent.name, "status": "failed",
                   "total": 0, "completed": 0, "images": [],
                   "errors": [{"task": "*", "error": "manifest corrupted"}]}
        if job.get("status") == "running":
            job = _mark_interrupted(job)
        else:
            continue
        job["lock"] = threading.Lock()
        _save_manifest(job)
        log.info("reconciled interrupted job %s -> %s", job["id"], job["status"])
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Cinematic Montage Service", version="1.1.0",
              lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def require_token(request: Request):
    if not SERVICE_TOKEN:
        return  # достижимо только при явном ALLOW_NO_AUTH=1
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not hmac.compare_digest(token, SERVICE_TOKEN):
        raise HTTPException(401, "invalid or missing bearer token")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/styles", dependencies=[Depends(require_token)])
def list_styles():
    return {k: v["title"] for k, v in STYLES.items()}


@app.get("/api/models", dependencies=[Depends(require_token)])
def list_models():
    return {"default": DEFAULT_MODEL_ID,
            "models": {k: v["title"] for k, v in MODELS.items()},
            "max_tasks_per_job": MAX_TASKS_PER_JOB}


def _load_job(job_id: str) -> dict | None:
    with jobs_lock:
        job = jobs.get(job_id)
    if job:
        return job
    manifest = _job_dir(job_id) / "manifest.json"
    if not manifest.exists():
        return None
    try:
        job = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        job = {"id": job_id, "status": "failed", "total": 0, "completed": 0,
               "images": [], "errors": [{"task": "*", "error": "manifest corrupted"}]}
    if job.get("status") == "running":
        # задачи в очередь не возвращаются после рестарта — честно фейлим
        job = _mark_interrupted(job)
    job["lock"] = threading.Lock()
    with jobs_lock:
        job = jobs.setdefault(job_id, job)
    return job


def _evict_terminal_jobs():
    with jobs_lock:
        if len(jobs) <= MAX_JOBS_IN_MEMORY:
            return
        for key in [k for k, v in list(jobs.items())
                    if v.get("status") in ("done", "partial", "failed")]:
            jobs.pop(key, None)
            if len(jobs) <= MAX_JOBS_IN_MEMORY:
                break


def _run_task(job_id: str, task: dict):
    global pending_tasks
    try:
        job = _load_job(job_id)
        if job is None or job.get("status") != "running":
            return
        provider_fn = PROVIDERS[job["provider"]]
        name = f"{task['style']}_{task['aspect'].replace(':', 'x')}_v{task['variant']}.png"
        try:
            source = _job_dir(job_id) / "source.bin"
            raw = provider_fn(task["prompt"], source.read_bytes(), job["image_mime"],
                              task["aspect"], job["image_size"], job.get("model"))
            _atomic_write_bytes(_job_dir(job_id) / name, raw)
            result = {"name": name, "style": task["style"], "aspect": task["aspect"],
                      "variant": task["variant"],
                      "url": f"/api/jobs/{job_id}/images/{name}"}
            error = None
        except ProviderError as e:
            log.warning("job %s task %s provider error: %s", job_id, name, e)
            result, error = None, {"task": name, "error": str(e)}
        except Exception:
            log.exception("job %s task %s internal error", job_id, name)
            result, error = None, {"task": name, "error": "internal error (see server logs)"}
        with job["lock"]:
            job["completed"] += 1
            if result:
                job["images"].append(result)
            if error:
                job["errors"].append(error)
            if job["completed"] >= job["total"]:
                job["status"] = ("done" if not job["errors"]
                                 else "partial" if job["images"] else "failed")
            _save_manifest(job)
        if job.get("status") != "running":
            _evict_terminal_jobs()
    finally:
        with pending_lock:
            pending_tasks -= 1


@app.post("/api/jobs", dependencies=[Depends(require_token)])
async def create_job(image: UploadFile = File(...), payload: str = Form("{}")):
    global pending_tasks
    # Лимит размера — потоково, не читая всё тело заранее
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    chunks, total = [], 0
    while chunk := await image.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f"image larger than {MAX_UPLOAD_MB} MB")
        chunks.append(chunk)
    image_bytes = b"".join(chunks)
    if not image_bytes:
        raise HTTPException(422, "empty image")
    image_mime = image.content_type or "image/jpeg"
    if image_mime not in ALLOWED_MIME:
        raise HTTPException(415, f"unsupported image type {image_mime}")

    try:
        cfg = json.loads(payload)
        assert isinstance(cfg, dict)
    except (json.JSONDecodeError, AssertionError):
        raise HTTPException(422, "payload must be a JSON object")

    content = str(cfg.get("prompt", "")).strip()
    style_keys = list(dict.fromkeys(cfg.get("styles") or []))   # dedup, порядок сохраняем
    custom_style = str(cfg.get("custom_style", "")).strip() or None
    aspects = list(dict.fromkeys(cfg.get("aspects") or ["16:9"]))
    image_size = str(cfg.get("image_size", "2K"))
    identity_lock = bool(cfg.get("identity_lock", True))
    try:
        variants = int(cfg.get("variants", 1))
    except (TypeError, ValueError):
        raise HTTPException(422, "variants must be an integer 1..4")

    # Выбор нейросети: либо model_id из реестра, либо provider+model вручную
    model_id = cfg.get("model_id")
    if model_id is not None:
        if model_id not in MODELS:
            raise HTTPException(422, f"unknown model_id {model_id!r}")
        provider = MODELS[model_id]["provider"]
        model = MODELS[model_id]["model"]
    else:
        provider = str(cfg.get("provider", os.environ.get("PROVIDER", "openrouter")))
        model = cfg.get("model")

    if provider not in PROVIDERS:
        raise HTTPException(422, f"unknown provider {provider!r}")
    if image_size not in ALLOWED_SIZES:
        raise HTTPException(422, f"image_size must be one of {sorted(ALLOWED_SIZES)}")
    bad = [a for a in aspects if a not in ALLOWED_ASPECTS]
    if bad:
        raise HTTPException(422, f"unsupported aspects {bad}")
    unknown = [s for s in style_keys if s not in STYLES]
    if unknown:
        raise HTTPException(422, f"unknown styles {unknown}")
    if not (1 <= variants <= 4):
        raise HTTPException(422, "variants must be an integer 1..4")
    if not content and not custom_style and not style_keys:
        raise HTTPException(422, "prompt, custom_style or styles required")

    # Комбинации: каждый выбранный пресет (или один «без пресета») × аспекты × дубли
    styles_iter = style_keys or [None]
    tasks = []
    for style_key in styles_iter:
        for aspect in aspects:
            for v in range(1, variants + 1):
                tasks.append({
                    "style": style_key or "custom",
                    "aspect": aspect,
                    "variant": v,
                    "prompt": build_prompt(content, style_key, custom_style, identity_lock),
                })
    if len(tasks) > MAX_TASKS_PER_JOB:
        raise HTTPException(422, f"too many combinations: {len(tasks)} > {MAX_TASKS_PER_JOB}")

    # Backpressure: не даём очереди (и памяти, и кредитам провайдера) расти бесконечно
    with pending_lock:
        if pending_tasks + len(tasks) > MAX_PENDING_TASKS:
            raise HTTPException(429, "generation queue is full, retry later")
        pending_tasks += len(tasks)

    try:
        job_id = uuid.uuid4().hex[:12]
        job_dir = _job_dir(job_id)
        job_dir.mkdir(parents=True)
        # исходник — на диск: очередь не таскает байты, рестарт не теряет вход
        _atomic_write_bytes(job_dir / "source.bin", image_bytes)
        job = {
            "id": job_id, "status": "running", "provider": provider, "model": model,
            "image_mime": image_mime, "image_size": image_size,
            "total": len(tasks), "completed": 0,
            "images": [], "errors": [], "lock": threading.Lock(),
        }
        with jobs_lock:
            jobs[job_id] = job
        _save_manifest(job)
    except BaseException:
        with pending_lock:
            pending_tasks -= len(tasks)
        raise

    for task in tasks:
        executor.submit(_run_task, job_id, task)
    return {"job_id": job_id, "total": len(tasks)}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str):
    if not SAFE_NAME.match(job_id):
        raise HTTPException(404, "job not found")
    job = _load_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    with job["lock"]:
        return JSONResponse({k: v for k, v in job.items() if k != "lock"})


@app.get("/api/jobs/{job_id}/images/{name}", dependencies=[Depends(require_token)])
def get_image(job_id: str, name: str):
    if not (SAFE_NAME.match(job_id) and SAFE_NAME.match(name) and name.endswith(".png")):
        raise HTTPException(404, "not found")
    path = (_job_dir(job_id) / name).resolve()
    if not (path.is_relative_to(DATA_DIR) and path.is_file()):
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="image/png")


app.mount("/", StaticFiles(directory=pathlib.Path(__file__).parent / "static", html=True))
