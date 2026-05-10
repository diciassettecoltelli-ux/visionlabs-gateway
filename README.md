# Visionlabs Gateway

Public backend for Vision generation.

## Purpose

This service exposes the Vision job API used by the public site and runs the
generation providers server-side. Video generation can use the official Kling,
Seedance, or Google lanes when API keys are configured. Image generation uses
OpenAI first when `OPENAI_API_KEY` is present, then falls back to the existing
Kling/Google image lanes.

## Endpoints

- `GET /api/health`
- `GET /api/engine/status`
- `POST /api/engine/prepare`
- `POST /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /generated/...`

## Deploy

This repo includes [`render.yaml`](/Users/a1/visionlabs-gateway/render.yaml) for Render.

Required secrets on the deploy target:

- `VISION_GATEWAY_PUBLIC_BASE_URL`
- `KLING_ACCESS_KEY`
- `KLING_SECRET_KEY`
- `VISION_KLING_COOKIE_HEADER`
- `VISION_KLING_REQUEST_HEADERS_JSON`
- `VISION_KLING_SUBMIT_PAYLOAD_JSON`
- `OPENAI_API_KEY`

OpenAI image controls:

- `VISION_GATEWAY_DEFAULT_IMAGE_PROVIDER=auto` uses OpenAI first when it is
  ready, then falls back to Kling/Google. Set it to `openai` to force OpenAI
  image generation only.
- `OPENAI_IMAGE_MODEL` defaults to `gpt-image-1.5`.
- `OPENAI_IMAGE_SIZE=auto` lets Vision map 9:16, 16:9, and 1:1 requests to the
  matching OpenAI image size.
- `OPENAI_IMAGE_QUALITY=auto` lets Vision map pack quality to the API quality.

Optional Kling API controls:

- `VISION_KLING_API_FIRST=true` keeps official Kling API video first when the
  gateway provider is `auto`.
- `KLING_API_VIDEO_MODEL` defaults to `kling-v3-omni` for native 15s video when
  available from the official Kling API.
- `KLING_API_FALLBACK_VIDEO_MODEL` defaults to `kling-v2-1-master`; Vision falls
  back to this model automatically if Omni is not supported by the API account.
- `KLING_API_BASE_URL` defaults to `https://api-singapore.klingai.com`.

Credit/account storage:

- `VISION_ACCESS_STORAGE=postgres` stores Vision pack balances in Postgres.
- `ACCESS_DATABASE_URL` can point to the account ledger database. If omitted,
  the gateway reuses `TRACKING_DATABASE_URL` or `DATABASE_URL`.
- Runtime JSON access storage is only a local fallback and should not be used as
  the production source of truth.
