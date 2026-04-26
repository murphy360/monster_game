"""Route handlers for saved level listing and retrieval."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..storage import delete_level, get_level, list_levels, update_level

router = APIRouter()


class LevelSummary(BaseModel):
    id: str
    title: str
    theme: str
    created_at: str
    updated_at: str | None = None
    version: int = 1
    is_current: bool = True
    versions_count: int = 1


class ApplyPreviewRequest(BaseModel):
    window_key_color: str
    windows: list[dict[str, Any]]
    processed_background_url: str
    preview_candidate: dict[str, Any] | None = None


def _normalize_hex_color(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


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


@router.delete("/levels/{level_id}", status_code=204)
async def remove_level(level_id: str) -> None:
    """Delete a saved level."""
    existing = get_level(level_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Level not found")

    deleted = delete_level(level_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete level")


@router.put("/levels/{level_id}/apply-preview")
async def apply_preview(level_id: str, payload: ApplyPreviewRequest) -> Any:
    """Persist preview-derived mask output onto an existing saved level."""
    current = get_level(level_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Level not found")

    key_color = _normalize_hex_color(payload.window_key_color)
    windows = payload.windows if isinstance(payload.windows, list) else []
    processed_background_url = str(payload.processed_background_url or "")
    if not key_color or not processed_background_url:
        raise HTTPException(status_code=400, detail="window_key_color and processed_background_url are required")

    existing_decision = current.get("color_decision")
    color_decision = dict(existing_decision) if isinstance(existing_decision, dict) else {}
    existing_candidates = color_decision.get("candidate_scores")
    candidate_scores = list(existing_candidates) if isinstance(existing_candidates, list) else []

    incoming_candidate = payload.preview_candidate if isinstance(payload.preview_candidate, dict) else None
    if incoming_candidate:
        filtered = [
            row for row in candidate_scores
            if str((row or {}).get("key_color", "")).upper() != key_color
        ]
        filtered.append({**incoming_candidate, "key_color": key_color})
        candidate_scores = filtered

    color_decision.update(
        {
            "selected_key_color": key_color,
            "final_mask_removal_color": key_color,
            "selected_windows": windows,
            "selected_window_count": len(windows),
            "candidate_scores": candidate_scores,
            "manual_preview_applied": True,
        }
    )

    updated = update_level(
        level_id,
        {
            "window_key_color": key_color,
            "background_url": processed_background_url,
            "windows": windows,
            "color_decision": color_decision,
        },
    )

    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to persist preview")

    return updated
