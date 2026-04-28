# Visionlabs Gateway

Public backend for Vision generation.

## Purpose

This service exposes the Vision job API used by the public site and runs the
Kling integrations server-side. Video generation can use the official Kling API
when API keys are configured; image generation can still use the Kling web
session bridge for the unlimited image lane.

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
