"""Cinematic Montage Service — прод-сервис кинематографичного редактирования фото.

POST /api/jobs      — загрузить фото + конфиг, получить job_id
GET  /api/jobs/{id} — статус и список готовых картинок
GET  /api/jobs/{id}/images/{name} — скачать картинку
GET  /api/styles    — доступные пресеты
GET  /healthz       — liveness

Auth: если задан SERVICE_TOKEN, все /api/* требуют Authorization: Bearer <token>.
"""
import json
import os
import pathlib
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from providers import DEFAULT_MODEL_ID, MODELS, PROVIDERS, ProviderError
from styles import STYLES, build_prompt

DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR", "./data")).resolve()
WORKERS = int(os.environ.get("WORKERS", "4"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "15"))
MAX_TASKS_PER_JOB = int(os.environ.get("MAX_TASKS_PER_JOB", "24"))
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")

ALLOWED_ASPECTS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"}
ALLOWED_SIZES = {"1K", "2K", "4K"}
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Cinematic Montage Service", version="1.0.0")
executor = ThreadPoolExecutor(max_workers=WORKERS)
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def require_token(request: Request):
    if not SERVICE_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {SERVICE_TOKEN}":
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
            "models": {k: v["title"] for k, v in MODELS.items()}}


def _job_dir(job_id: str) -> pathlib.Path:
    return DATA_DIR / job_id


def _save_manifest(job: dict):
    path = _job_dir(job["id"]) / "manifest.json"
    snapshot = {k: v for k, v in job.items() if k != "lock"}
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))


def _load_job(job_id: str) -> dict | None:
    with jobs_lock:
        job = jobs.get(job_id)
    if job:
        return job
    manifest = _job_dir(job_id) / "manifest.json"
    if manifest.exists():
        job = json.loads(manifest.read_text())
        job["lock"] = threading.Lock()
        with jobs_lock:
            jobs.setdefault(job_id, job)
        return jobs[job_id]
    return None


def _run_task(job_id: str, task: dict, image_bytes: bytes, image_mime: str):
    job = _load_job(job_id)
    if job is None:
        return
    provider_fn = PROVIDERS[job["provider"]]
    name = f"{task['style']}_{task['aspect'].replace(':', 'x')}_v{task['variant']}.png"
    try:
        raw = provider_fn(task["prompt"], image_bytes, image_mime,
                          task["aspect"], job["image_size"], job.get("model"))
        (_job_dir(job_id) / name).write_bytes(raw)
        result = {"name": name, "style": task["style"], "aspect": task["aspect"],
                  "variant": task["variant"], "url": f"/api/jobs/{job_id}/images/{name}"}
        error = None
    except ProviderError as e:
        result, error = None, {"task": name, "error": str(e)}
    except Exception as e:  # не роняем воркер молча
        result, error = None, {"task": name, "error": f"internal: {e}"}
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


@app.post("/api/jobs", dependencies=[Depends(require_token)])
async def create_job(image: UploadFile = File(...), payload: str = Form("{}")):
    image_bytes = await image.read()
    if len(image_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"image larger than {MAX_UPLOAD_MB} MB")
    image_mime = image.content_type or "image/jpeg"
    if image_mime not in ALLOWED_MIME:
        raise HTTPException(415, f"unsupported image type {image_mime}")

    try:
        cfg = json.loads(payload)
        assert isinstance(cfg, dict)
    except (json.JSONDecodeError, AssertionError):
        raise HTTPException(422, "payload must be a JSON object")

    content = str(cfg.get("prompt", "")).strip()
    style_keys = cfg.get("styles") or []
    custom_style = str(cfg.get("custom_style", "")).strip() or None
    aspects = cfg.get("aspects") or ["16:9"]
    variants = int(cfg.get("variants", 1))
    image_size = str(cfg.get("image_size", "2K"))
    identity_lock = bool(cfg.get("identity_lock", True))

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
        raise HTTPException(422, "variants must be 1..4")
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

    job_id = uuid.uuid4().hex[:12]
    _job_dir(job_id).mkdir(parents=True)
    job = {
        "id": job_id, "status": "running", "provider": provider, "model": model,
        "image_size": image_size, "total": len(tasks), "completed": 0,
        "images": [], "errors": [], "lock": threading.Lock(),
    }
    with jobs_lock:
        jobs[job_id] = job
    _save_manifest(job)
    for task in tasks:
        executor.submit(_run_task, job_id, task, image_bytes, image_mime)
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
    path = _job_dir(job_id) / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="image/png")


app.mount("/", StaticFiles(directory=pathlib.Path(__file__).parent / "static", html=True))
