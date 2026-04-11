# Vision Gateway Deploy

This service powers Vision prompt generation outside the local Mac desktop.

## What it does

- serves `/api/health`
- serves `/api/engine/status`
- serves `/api/engine/prepare`
- serves `/api/jobs`
- serves generated mp4 assets under `/generated/...`

## Required environment variables

- `VISION_GATEWAY_HOST`
  - recommended: `0.0.0.0`
- `VISION_GATEWAY_PUBLIC_BASE_URL`
  - example: `https://vision-gateway.onrender.com`
- `VISION_GATEWAY_CORS_ALLOW_ORIGINS`
  - example: `https://visionlabs.cloud,https://www.visionlabs.cloud`
- `VISION_GATEWAY_VISION_ROOT`
  - optional; defaults to a sibling `vision` folder if present
- `VISION_KLING_COOKIE_HEADER`
  - full cookie header copied from a real Kling web submit request
- `VISION_KLING_REQUEST_HEADERS_JSON`
  - JSON object of stable request headers from a real Kling web submit request
- `VISION_KLING_SUBMIT_PAYLOAD_JSON`
  - JSON object matching a real Kling Omni submit payload template

## Render notes

The current `render.yaml` defines a single Python web service for the gateway.

Once Render gives you a public backend URL, update:

- `/Users/a1/vision/vision-config.js`

Set:

```js
window.VISION_API_BASE = "https://YOUR-RENDER-SERVICE.onrender.com";
```

Then redeploy the Netlify frontend.

## Current blocker

Render deployment requires a git-backed repository or an already configured Render MCP/CLI flow.
This workspace currently has no git remote configured.
