# Cinematic Montage Service

Прод-сервис кинематографичного редактирования фото: люди на снимке сохраняются
1-в-1, в сцену добавляются свет, глубина и новые объекты по промпту.
Провайдеры: **OpenRouter** (Nano Banana Pro / Nano Banana) и **Gemini API напрямую**
(свой Google-ключ, нужен проект с биллингом). Выбор нейросети — при каждой генерации.

## Быстрый старт (локально)

```bash
cd photo-montage/service
pip install -r requirements.txt
cp .env.example .env            # вписать OPENROUTER_API_KEY и SERVICE_TOKEN
set -a; source .env; set +a
uvicorn app:app --port 8853     # либо ALLOW_NO_AUTH=1 uvicorn ... для локалки
# UI: http://localhost:8853
```

Без `SERVICE_TOKEN` сервис не стартует (fail-closed) — для разработки без
токена нужно явно передать `ALLOW_NO_AUTH=1`.

## Прод (Docker)

```bash
cd photo-montage/service
cp .env.example .env            # ключи + SERVICE_TOKEN
docker compose up -d --build
# сервис на :8853, картинки живут в volume montage-data
```

За реверс-прокси (nginx/caddy) повесить на поддомен, например `montage.postim.life`.

## API

Все `/api/*` (кроме `/healthz`) требуют `Authorization: Bearer $SERVICE_TOKEN`,
если переменная задана.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/api/styles` | пресеты стилей `{key: title}` |
| GET | `/api/models` | нейросети `{default, models:{id: title}}` |
| POST | `/api/jobs` | создать задачу (multipart: `image` + `payload`) |
| GET | `/api/jobs/{id}` | статус: `running/done/partial/failed`, картинки, ошибки |
| GET | `/api/jobs/{id}/images/{name}` | скачать PNG |
| GET | `/healthz` | liveness |

`payload` — JSON-строка:

```json
{
  "prompt": "add two female police officers behind the men and a lowered UAZ Patriot police pickup",
  "styles": ["golden", "neonnoir"],
  "aspects": ["16:9", "9:16"],
  "variants": 1,
  "image_size": "2K",
  "model_id": "nano-banana-pro",
  "identity_lock": true
}
```

- `model_id` — из `/api/models` (`nano-banana-pro`, `nano-banana`,
  `gemini-pro-direct`, `gemini-flash-direct`); либо вручную `provider` + `model`.

### BYOK: ключ провайдера на запрос

Заголовок `X-Provider-Key` на `POST /api/jobs` переопределяет центральный ключ
провайдера (`OPENROUTER_API_KEY`/`GEMINI_API_KEY`) **только для этой задачи** —
так платформа может генерировать на ключе конкретного тенанта. Ключ живёт
только в памяти на время job, никогда не пишется в манифест на диск. Если
заголовок не прислан — используется центральный ключ из env (как раньше).
- `styles` можно опустить и передать только `prompt` и/или `custom_style`.
- Комбинаций (стили × ориентации × дубли) — не больше `MAX_TASKS_PER_JOB` (24).

## Интеграция с postim.life (Next.js API route)

```ts
// app/api/montage/route.ts
export async function POST(req: Request) {
  const form = await req.formData(); // image + payload от фронта
  const r = await fetch(`${process.env.MONTAGE_URL}/api/jobs`, {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.MONTAGE_TOKEN}` },
    body: form,
  });
  return Response.json(await r.json(), { status: r.status });
}
```

Статус и картинки проксируются так же (`GET /api/jobs/{id}`), либо фронт ходит
в сервис напрямую с тем же Bearer-токеном.

## Эксплуатация

- **Backpressure:** очередь генераций ограничена `MAX_PENDING_TASKS` (96) —
  при переполнении `POST /api/jobs` отвечает 429, клиенту нужно повторить позже.
- **Рестарты:** задачи, находившиеся в очереди в момент перезапуска, честно
  помечаются `failed`/`partial` с ошибкой «interrupted by service restart» —
  зависших навсегда `running` не бывает. Записи на диск атомарные.
- **Диск:** каталоги задач в `DATA_DIR` не удаляются автоматически — повесьте
  крон-чистку по возрасту, например: `find /data -maxdepth 1 -mtime +14 -exec rm -rf {} +`.
- **OpenAPI-доки** (`/docs`, `/openapi.json`) в проде выключены.

## Добавить нейросеть

Одна строка в `providers.py` → `MODELS` (title + provider + model id).
Новый провайдер = функция с той же сигнатурой в `PROVIDERS`.
