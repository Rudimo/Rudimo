# Кинематографичный фотомонтаж (Nano Banana Pro via OpenRouter)

Редактирование исходного фото: два парня остаются 1-в-1 как в оригинале,
добавляются кинематографичный свет, две женщины-полицейские в парадной форме
и заниженный УАЗ Патриот «ПОЛИЦИЯ». 5 стилей × 2 ориентации (16:9 и 9:16) = 10 картинок.

## Запуск

```bash
cd photo-montage
pip install requests
export OPENROUTER_API_KEY="sk-or-..."      # ключ с openrouter.ai
# положить фото рядом со скриптом:
#   FullSizeRender.jpeg  (или указать свой путь: export BASE_IMAGE=/path/to/photo.jpg)
python generate.py
```

Результаты появятся в `photo-montage/out/`.

## Стили

| Файл | Стиль |
|---|---|
| `01_golden_*` | тёплый киношный teal-and-orange, ночная заправка |
| `02_neonnoir_*` | неон-нуар, мигалка включена, мокрый асфальт |
| `03_actionposter_*` | постер боевика, героический нижний ракурс |
| `04_realism_*` | документальный фотореализм, максимально естественно |
| `05_comedy_*` | комедийный блокбастер, яркий и глянцевый |

## Настройки в `generate.py`

- `MODEL` — по умолчанию `google/gemini-3-pro-image-preview` (Nano Banana Pro);
  дешевле/быстрее: `google/gemini-2.5-flash-image`
- `VARIANTS_PER_COMBO` — подними до 2–3, чтобы выбрать лучший дубль
- `IMAGE_SIZE` — `2K` по умолчанию
