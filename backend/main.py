"""FastAPI application entry-point."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes.generate_level import router as generate_level_router
from .routes.levels import router as levels_router
from .routes.serve_assets import router as serve_assets_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Monster Game API",
    description="AI-powered backend for the Whack-A-Monster touch-screen game.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate_level_router)
app.include_router(levels_router)
app.include_router(serve_assets_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
