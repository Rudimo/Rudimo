"""Провайдеры генерации: OpenRouter (основной) и Gemini API напрямую.

Оба принимают один и тот же контракт:
    generate(prompt, image_bytes, image_mime, aspect, image_size, model) -> bytes (PNG/JPEG)
Бросают ProviderError с понятным сообщением.
"""
import base64
import os
import time

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_MODEL_OPENROUTER = "google/gemini-3-pro-image-preview"
DEFAULT_MODEL_GEMINI = "gemini-3-pro-image-preview"

RETRIABLE = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    pass


def _retrying_post(url, *, headers, json_payload, timeout, attempts=3):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(url, headers=headers, json=json_payload, timeout=timeout)
        except requests.RequestException as e:
            last = f"network error: {e}"
        else:
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in RETRIABLE:
                raise ProviderError(last)
        if attempt < attempts:
            time.sleep(5 * attempt)
    raise ProviderError(last or "unknown error")


def generate_openrouter(prompt, image_bytes, image_mime, aspect, image_size, model=None):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ProviderError("OPENROUTER_API_KEY is not set")
    data_url = f"data:{image_mime};base64,{base64.b64encode(image_bytes).decode()}"
    payload = {
        "model": model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL_OPENROUTER),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": aspect, "image_size": image_size},
    }
    r = _retrying_post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json_payload=payload, timeout=300)
    msg = r.json()["choices"][0]["message"]
    imgs = msg.get("images") or []
    if not imgs:
        text = str(msg.get("content") or "")[:200]
        raise ProviderError(f"no image in response: {text}")
    url = imgs[0]["image_url"]["url"]  # data:image/png;base64,...
    return base64.b64decode(url.split(",", 1)[1])


def generate_gemini(prompt, image_bytes, image_mime, aspect, image_size, model=None):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ProviderError("GEMINI_API_KEY is not set")
    payload = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inlineData": {"mimeType": image_mime,
                            "data": base64.b64encode(image_bytes).decode()}},
        ]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect, "imageSize": image_size},
        },
    }
    url = GEMINI_URL.format(model=model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL_GEMINI))
    r = _retrying_post(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json_payload=payload, timeout=300)
    for cand in r.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            blob = part.get("inlineData")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"])
    raise ProviderError("no image in Gemini response")


PROVIDERS = {
    "openrouter": generate_openrouter,
    "gemini": generate_gemini,
}

# Реестр моделей для выбора в UI/API: id -> (title, provider, model).
# Добавить новую нейросеть = дописать строку здесь.
MODELS = {
    "nano-banana-pro": {
        "title": "Nano Banana Pro · OpenRouter",
        "provider": "openrouter", "model": "google/gemini-3-pro-image-preview",
    },
    "nano-banana": {
        "title": "Nano Banana (дешевле) · OpenRouter",
        "provider": "openrouter", "model": "google/gemini-2.5-flash-image",
    },
    "gemini-pro-direct": {
        "title": "Gemini 3 Pro Image · свой Google-ключ",
        "provider": "gemini", "model": "gemini-3-pro-image-preview",
    },
    "gemini-flash-direct": {
        "title": "Gemini Flash Image · свой Google-ключ",
        "provider": "gemini", "model": "gemini-2.5-flash-image",
    },
}
DEFAULT_MODEL_ID = "nano-banana-pro"
