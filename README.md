# Visionlabs Gateway

Public backend for Vision generation.

## Purpose

This service exposes the Vision job API used by the public site and runs the
Kling session bridge server-side.

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
- `VISION_KLING_COOKIE_HEADER`
- `VISION_KLING_REQUEST_HEADERS_JSON`
- `VISION_KLING_SUBMIT_PAYLOAD_JSON`
