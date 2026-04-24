"""Route handlers for saved level listing and retrieval."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..storage import get_level, list_levels

router = APIRouter()


class LevelSummary(BaseModel):
    id: str
    title: str
    theme: str
    created_at: str


@router.get("/levels", response_model=list[LevelSummary])
async def get_levels() -> Any:
    """List all saved levels, newest first."""
    return list_levels()


@router.get("/levels/{level_id}")
async def load_level(level_id: str) -> Any:
    """Return the full data for a saved level."""
    record = get_level(level_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Level not found")
    return record
