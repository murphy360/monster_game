# Contributing

Quick reference for working on this repo, human or AI-assisted.

## Backend (FastAPI, Python 3.12)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements-dev.txt

ruff check .            # lint
ruff format .           # auto-format
pytest                  # run tests (from repo root)
```

Run from the repo root: `ruff check backend`, `ruff format backend`, `pytest`.

## Frontend (React + Vite)

```bash
cd frontend
npm install

npm run lint             # ESLint
npm run lint:fix         # ESLint with autofix
npm run format            # Prettier --write
npm run format:check      # Prettier --check
npm run build              # production build (also validates the app compiles)
```

## Before opening a PR

Run the backend and frontend checks above — CI runs the same commands (`.github/workflows/ci.yml`)
on every push and pull request against `main`, plus a Docker build smoke test for both images.

## Docker images

On every push to `main` (and version tags like `v1.2.3`), CI builds and pushes both images to the
GitHub Container Registry:

- `ghcr.io/murphy360/monster_game-backend`
- `ghcr.io/murphy360/monster_game-frontend`

Tags include `latest` (default branch), the branch name, the short commit SHA, and semver tags for
`v*.*.*` pushes.

## Conventions

- Keep route handlers in `backend/routes/`, AI provider logic behind the `AIGenerator` interface in
  `backend/ai/base.py` (see `backend/ai/dependencies.py` for how the concrete provider is wired up).
- Frontend components live in `frontend/src/components/`.
- Don't commit `.env` files or API keys — copy `backend/.env.example` to `backend/.env` locally.
