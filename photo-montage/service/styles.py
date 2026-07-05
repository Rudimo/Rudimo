"""Пресеты кинематографичных стилей и сборка промптов.

Пресет отвечает за свет/цвет/настроение кадра, контент-промпт пользователя —
за то, ЧТО добавить в сцену. identity-lock всегда защищает исходных людей.
"""

IDENTITY_LOCK = (
    "CRITICAL identity lock: this is a photo EDIT of the provided source image. "
    "Keep every person from the source EXACTLY as captured — same faces, same facial "
    "features, same expressions, same skin, same exact body poses and hand positions, "
    "same clothing and colors, same headwear and footwear. Do NOT regenerate, restyle, "
    "beautify, slim or age them. Preserve the original camera framing and angle. "
    "Only relight the scene, add cinematic depth, and composite requested new elements "
    "with correct perspective, scale and lighting. Render any requested text accurately, "
    "including Cyrillic. No plastic skin, no cartoon, no extra or deformed limbs, "
    "no distorted faces, no watermark artifacts."
)

STYLES = {
    "golden": {
        "title": "Golden cinematic",
        "prompt": (
            "Cinematic film still. Warm golden practical lights mixed with cool night "
            "ambience. Lighting: warm directional key, cool ambient fill, subtle rim "
            "light separating the subjects, soft grounded contact shadows, light "
            "atmospheric haze. Grade: teal-and-orange cinematic, filmic contrast, "
            "gentle vignette, fine natural grain. Shallow depth of field, subjects "
            "tack-sharp, background creamy bokeh."
        ),
    },
    "neonnoir": {
        "title": "Neon noir",
        "prompt": (
            "Neon-noir night frame, hard chiaroscuro, cyan-and-teal palette, dramatic "
            "movie look. Deep pooled shadows, faint wet-ground reflections, crushed "
            "blacks, film grain, vignette. Shallow depth of field."
        ),
    },
    "actionposter": {
        "title": "Action poster",
        "prompt": (
            "Action-movie poster look, heroic and epic. Strong directional key, "
            "dramatic rim/backlight, atmospheric haze and light beams, high-contrast "
            "blockbuster teal-orange grade, glossy highlights, fine grain. Low hero "
            "angle, shallow depth of field, subjects razor-sharp."
        ),
    },
    "realism": {
        "title": "Documentary realism",
        "prompt": (
            "Naturalistic, believable documentary photography, restrained and subtle. "
            "Lighting true to the original scene, gentle shadows, mild haze. Subtle "
            "filmic grade, natural colors, light grain, slight vignette. Moderate "
            "shallow depth of field. Keep it photorealistic."
        ),
    },
    "comedy": {
        "title": "Comedy blockbuster",
        "prompt": (
            "Playful cinematic comedy-blockbuster, vibrant and glossy, fun energy. "
            "Warm bright key, cool fill, nice rim light, soft shadows, subtle haze. "
            "Vibrant warm glossy grade, rich but clean contrast, fine grain. Shallow "
            "depth of field, subjects sharp."
        ),
    },
}


def build_prompt(content: str, style_key: str | None, custom_style: str | None,
                 identity_lock: bool = True) -> str:
    parts = []
    if style_key:
        parts.append(STYLES[style_key]["prompt"])
    if custom_style:
        parts.append(custom_style.strip())
    if content:
        parts.append(content.strip())
    if identity_lock:
        parts.append(IDENTITY_LOCK)
    return " ".join(p for p in parts if p)
