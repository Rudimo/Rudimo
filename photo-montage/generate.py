# --------------------------------------------------------------------------
# Кинематографичный фотомонтаж на базе исходной фотографии.
# Модель: Nano Banana Pro (Gemini 3 Pro Image) через OpenRouter.
# Это ИМЕННО РЕДАКТИРОВАНИЕ исходного фото: два парня остаются на 100%
# как в оригинале (лица, позы, одежда — не трогать!). Добавляем свет,
# глубину кадра, двух русских женщин-полицейских в парадной форме (юбка,
# китель, фуражка) и заниженный УАЗ Патриот в раскраске "ПОЛИЦИЯ".
#
# 5 стилей × 2 ориентации (16:9 и 9:16) = 10 картинок за прогон.
# Результаты складываются в папку ./out/
#
# КАК ЗАПУСТИТЬ:
#   1) pip install requests
#   2) export OPENROUTER_API_KEY="sk-or-..."   (ключ с openrouter.ai)
#   3) положить фотографию рядом со скриптом (см. BASE_IMAGE)
#   4) python generate.py
# --------------------------------------------------------------------------

import os, base64, mimetypes, pathlib, time
import requests

# ===== НАСТРОЙКИ =====
API_KEY   = os.environ["OPENROUTER_API_KEY"]
MODEL     = "google/gemini-3-pro-image-preview"   # Nano Banana Pro
# fallback если нужен подешевле/побыстрее: "google/gemini-2.5-flash-image"
BASE_IMAGE = os.environ.get("BASE_IMAGE", "FullSizeRender.jpeg")  # <-- ПУТЬ К ФОТКЕ
OUT_DIR    = "out"
ASPECTS    = ["16:9", "9:16"]        # горизонталь + вертикаль
IMAGE_SIZE = "2K"
VARIANTS_PER_COMBO = 1               # подними до 2-3, чтобы выбрать лучший дубль
URL = "https://openrouter.ai/api/v1/chat/completions"

# ===== ОБЩИЕ БЛОКИ (не повторяем 5 раз) =====
GLOBAL = (
    "CRITICAL identity lock: this is a photo EDIT of the provided source image. "
    "Keep the two men EXACTLY as in the source — same faces, same facial features, "
    "same expressions, same skin, same exact body poses and hand positions, same "
    "clothing and colors, same caps and footwear. Do NOT regenerate, restyle, "
    "beautify, slim or age their faces. Preserve the original low ground-level "
    "camera framing. Only relight the scene, add cinematic depth, and composite the "
    "new officers and vehicle with correct perspective, scale and lighting. Render "
    "all Cyrillic text accurately, especially 'ПОЛИЦИЯ'. No plastic skin, no cartoon, "
    "no extra or deformed limbs, no distorted faces, no watermark artifacts."
)
OFFICERS = (
    "Add two female Russian police officers standing behind and slightly flanking the "
    "two men with a professional, authoritative posture. They wear the official Russian "
    "police dress uniform: dark navy jacket, light-blue shirt, knee-length navy skirt, "
    "peaked cap with cockade, duty belt, 'ПОЛИЦИЯ' insignia. Tasteful and realistic."
)
VEHICLE = (
    "In the background between the men and the grey car, add a lowered UAZ Patriot pickup "
    "in Russian police livery — white body with a bold blue side stripe, blue-and-red roof "
    "lightbar, 'ПОЛИЦИЯ' written in Cyrillic on the doors — parked and softly out of focus."
)

# ===== 5 СТИЛЕЙ =====
STYLES = {
"01_golden": (
    "Cinematic film still at a Thai roadside charging station at night. Warm golden "
    "practical lights mixed with cool night ambience. Mood: classic 'they just got "
    "detained' scene. " + OFFICERS + " " + VEHICLE + " Lighting: warm directional key, "
    "cool ambient fill, subtle rim light separating the subjects, soft grounded contact "
    "shadows, light atmospheric haze. Grade: teal-and-orange cinematic, filmic contrast, "
    "gentle vignette, fine natural grain. Shallow depth of field, men tack-sharp, "
    "background creamy bokeh. " + GLOBAL
),
"02_neonnoir": (
    "Neon-noir night frame, hard chiaroscuro, cyan-and-teal palette, dramatic movie look. "
    + OFFICERS + " lit by the blue police lightbar and station neon. " + VEHICLE +
    " Its roof lightbar is ON, casting a blue glow and spilling light onto the ground. "
    "Deep pooled shadows, faint wet-ground reflections, crushed blacks, film grain, vignette. "
    "Low angle as original, shallow DOF. " + GLOBAL
),
"03_actionposter": (
    "Action-movie poster look, heroic and epic. " + OFFICERS.replace(
        "authoritative posture", "dynamic authoritative stance, one gesturing/commanding") +
    " " + VEHICLE.replace("parked and softly out of focus",
        "slightly angled for drama, lightbar on, prominent in the background") +
    " Strong directional key, dramatic rim/backlight, atmospheric haze and light beams, "
    "high-contrast blockbuster teal-orange grade, glossy highlights, fine grain. "
    "Even lower hero angle, shallow DOF, men razor-sharp. " + GLOBAL
),
"04_realism": (
    "Naturalistic, believable documentary night photography, restrained and subtle. "
    + OFFICERS.replace("standing behind and slightly flanking",
        "approaching from behind, candid mid-step,") + " " + VEHICLE +
    " Lighting true to the original night scene, soft key from station lights, gentle "
    "shadows, mild haze. Subtle filmic grade, natural colors, light grain, slight vignette. "
    "Low angle as original, moderate shallow DOF. Keep it photorealistic. " + GLOBAL
),
"05_comedy": (
    "Playful cinematic comedy-blockbuster, vibrant and glossy, fun energy. "
    + OFFICERS.replace("authoritative posture",
        "confident hands-on-hips power pose with a slight smirk") + " " + VEHICLE +
    " Warm bright key, cool fill, nice rim light, soft shadows, subtle haze. Vibrant warm "
    "glossy grade, rich but clean contrast, fine grain. Low angle as original, shallow DOF, "
    "men sharp. " + GLOBAL
),
}

# ===== ХЕЛПЕРЫ =====
def img_to_data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    b64 = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
    return f"data:{mime};base64,{b64}"

def generate(prompt, aspect, out_path, base_data_url):
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": base_data_url}},
            ],
        }],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": aspect, "image_size": IMAGE_SIZE},
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    r = requests.post(URL, headers=headers, json=payload, timeout=300)
    if r.status_code != 200:
        print(f"  ! HTTP {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
    data = r.json()
    msg = data["choices"][0]["message"]
    imgs = msg.get("images") or []
    if not imgs:
        print(f"  ! нет картинки в ответе: {str(msg.get('content') or '')[:120]}")
        return False
    url = imgs[0]["image_url"]["url"]                 # data:image/png;base64,....
    raw = base64.b64decode(url.split(",", 1)[1])
    pathlib.Path(out_path).write_bytes(raw)
    print(f"  ✓ {out_path}")
    return True

# ===== ЗАПУСК =====
def main():
    pathlib.Path(OUT_DIR).mkdir(exist_ok=True)
    base = img_to_data_url(BASE_IMAGE)
    total = len(STYLES) * len(ASPECTS) * VARIANTS_PER_COMBO
    n = 0
    for name, prompt in STYLES.items():
        for aspect in ASPECTS:
            tag = aspect.replace(":", "x")
            for v in range(1, VARIANTS_PER_COMBO + 1):
                n += 1
                out = f"{OUT_DIR}/{name}_{tag}_v{v}.png"
                print(f"[{n}/{total}] {name} {aspect} …")
                try:
                    generate(prompt, aspect, out, base)
                except Exception as e:
                    print(f"  ! ошибка: {e}")
                time.sleep(1)
    print("Готово. Смотри папку ./out")

if __name__ == "__main__":
    main()
