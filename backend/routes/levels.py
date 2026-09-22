"""Route handlers for saved level listing and retrieval."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ai.window_outline import outline_windows_from_image
from ..storage import delete_level, get_level, list_levels, update_level

router = APIRouter()
logger = logging.getLogger(__name__)


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
    cropped_background_url: str | None = None
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
    cropped_background_url = str(payload.cropped_background_url or "")
    if not key_color or not processed_background_url:
        raise HTTPException(
            status_code=400, detail="window_key_color and processed_background_url are required"
        )

    existing_decision = current.get("color_decision")
    color_decision = dict(existing_decision) if isinstance(existing_decision, dict) else {}
    existing_candidates = color_decision.get("candidate_scores")
    candidate_scores = list(existing_candidates) if isinstance(existing_candidates, list) else []

    incoming_candidate = payload.preview_candidate if isinstance(payload.preview_candidate, dict) else None
    if incoming_candidate:
        filtered = [
            row for row in candidate_scores if str((row or {}).get("key_color", "")).upper() != key_color
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
            "cropped_background_url": cropped_background_url or current.get("cropped_background_url", ""),
            "background_url": processed_background_url,
            "windows": windows,
            "color_decision": color_decision,
        },
    )

    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to persist preview")

    return updated


class ReprocessAllRequest(BaseModel):
    apply: bool = False


class LevelReprocessResult(BaseModel):
    id: str
    title: str
    old_window_count: int
    new_window_count: int
    changed: bool
    applied: bool
    error: str | None = None


def _windows_signature(windows: list[dict[str, Any]]) -> list[tuple[int, int, int, int]]:
    return sorted(
        (
            int((w or {}).get("x", 0)),
            int((w or {}).get("y", 0)),
            int((w or {}).get("width", 0)),
            int((w or {}).get("height", 0)),
        )
        for w in windows
    )


async def _reprocess_level(level_id: str, current: dict[str, Any], apply: bool) -> LevelReprocessResult:
    """Re-run window detection against one saved level's original image
    using the currently deployed algorithm.

    Dry-run by default (apply=False): reports what would change without
    touching saved data. Pass apply=True to persist the new result if the
    level's windows actually changed.
    """
    title = str(current.get("title") or "Untitled")
    old_windows = current.get("windows") if isinstance(current.get("windows"), list) else []

    image_url = str(current.get("original_background_url") or current.get("cropped_background_url") or "")
    key_color = str(current.get("boundary_color") or current.get("window_key_color") or "")

    if not image_url or not key_color:
        return LevelReprocessResult(
            id=level_id,
            title=title,
            old_window_count=len(old_windows),
            new_window_count=len(old_windows),
            changed=False,
            applied=False,
            error="Level has no original image or key color on record; skipped.",
        )

    try:
        outlined = await outline_windows_from_image(image_url, key_color)
    except Exception as exc:
        logger.warning("Reprocess failed for level %s: %s", level_id, exc)
        return LevelReprocessResult(
            id=level_id,
            title=title,
            old_window_count=len(old_windows),
            new_window_count=len(old_windows),
            changed=False,
            applied=False,
            error=f"Reprocess failed: {exc}",
        )

    new_windows = outlined.get("windows") if isinstance(outlined.get("windows"), list) else []
    changed = _windows_signature(old_windows) != _windows_signature(new_windows)
    applied = False

    if apply and changed:
        new_key_color = _normalize_hex_color(str(outlined.get("window_key_color") or key_color))
        existing_decision = current.get("color_decision")
        color_decision = dict(existing_decision) if isinstance(existing_decision, dict) else {}

        # The Mask Color Decision candidate table (and the "highlighted
        # windows" preview it drives in the review UI) reads from
        # candidate_scores - without refreshing the row for the resolved
        # color here, it keeps showing whatever window count/list generation
        # time produced, even though windows/selected_windows above are now
        # current. That mismatch is exactly what left the UI still showing
        # only 2 windows after a reprocess that found 10.
        existing_candidates = color_decision.get("candidate_scores")
        candidate_scores = list(existing_candidates) if isinstance(existing_candidates, list) else []
        window_areas = sorted(
            (int(w.get("width", 0)) * int(w.get("height", 0)) for w in new_windows), reverse=True
        )
        refreshed_candidate = {
            "key_color": new_key_color,
            "window_count": len(new_windows),
            "windows": new_windows,
            "total_area": sum(window_areas),
            "top_window_area": window_areas[0] if window_areas else 0,
            "top_window_areas": window_areas[:5],
            "largest_area": window_areas[0] if window_areas else 0,
        }
        candidate_scores = [
            row for row in candidate_scores if str((row or {}).get("key_color", "")).upper() != new_key_color
        ]
        candidate_scores.append(refreshed_candidate)

        color_decision.update(
            {
                "selected_key_color": new_key_color,
                "final_mask_removal_color": new_key_color,
                "selected_windows": new_windows,
                "selected_window_count": len(new_windows),
                "candidate_scores": candidate_scores,
                "bulk_reprocess_applied": True,
            }
        )
        updated = update_level(
            level_id,
            {
                "window_key_color": new_key_color,
                "cropped_background_url": str(
                    outlined.get("cropped_background_url") or current.get("cropped_background_url", "")
                ),
                "background_url": str(
                    outlined.get("processed_background_url") or current.get("background_url", "")
                ),
                "windows": new_windows,
                "color_decision": color_decision,
            },
        )
        applied = updated is not None
        if not applied:
            return LevelReprocessResult(
                id=level_id,
                title=title,
                old_window_count=len(old_windows),
                new_window_count=len(new_windows),
                changed=changed,
                applied=False,
                error="Reprocessed successfully but failed to persist.",
            )

    return LevelReprocessResult(
        id=level_id,
        title=title,
        old_window_count=len(old_windows),
        new_window_count=len(new_windows),
        changed=changed,
        applied=applied,
    )


@router.post("/levels/{level_id}/reprocess", response_model=LevelReprocessResult)
async def reprocess_level(level_id: str, payload: ReprocessAllRequest) -> Any:
    """Re-run window detection against a single saved level's original image."""
    current = get_level(level_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Level not found")

    return await _reprocess_level(level_id, current, payload.apply)


@router.post("/levels/reprocess-all", response_model=list[LevelReprocessResult])
async def reprocess_all_levels(payload: ReprocessAllRequest) -> Any:
    """Re-run window detection against every saved level's original image
    using the currently deployed algorithm.

    Dry-run by default (apply=False): reports what would change without
    touching saved data. Pass apply=True to persist the new result for any
    level whose windows actually changed.
    """
    results: list[LevelReprocessResult] = []
    for summary in list_levels():
        level_id = str(summary.get("id") or "")
        current = get_level(level_id) if level_id else None
        if current is None:
            continue

        results.append(await _reprocess_level(level_id, current, payload.apply))

    return results
