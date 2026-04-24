# Monster Game

Touchscreen Monster Mash game with:

- a FastAPI backend ([backend](backend))
- a React + Vite frontend ([frontend](frontend))

## Run Locally With Docker (Recommended)

### Prerequisites

- Docker Desktop installed
- A Gemini API key for AI generation routes

### 1) Configure your API key

Copy [backend/.env.example](backend/.env.example) to [backend/.env](backend/.env) and set your key:

```env
GEMINI_API_KEY=your_real_key_here

# Optional model overrides
GEMINI_TEXT_MODEL=gemini-2.5-flash
GEMINI_IMAGE_MODEL=gemini-2.5-flash-image
```

### 2) Build and start both services

From the repo root:

```powershell
docker compose --env-file backend/.env up -d --build
```

This starts:

- frontend at `http://localhost:5173`
- backend at `http://localhost:8001`

### 3) Test health endpoint

```powershell
curl http://localhost:8001/health
```

Expected response:

```json
{"status":"ok"}
```

### 4) Stop services

```powershell
docker compose down
```

## Notes

- Frontend requests (`/generate-level`, `/serve-assets`, `/health`) are proxied to the backend.
- For code changes, rebuild with `docker compose --env-file backend/.env up --build`.
- Backend model defaults are `gemini-2.5-flash` (text/vision) and `gemini-2.5-flash-image` (image generation).
- Window boundary recognition flow is documented in [docs/window-outlining-process.md](docs/window-outlining-process.md).
