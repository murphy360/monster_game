"""Persistent level storage using JSON files."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

LEVELS_DIR = os.environ.get("LEVELS_DIR", "/data/levels")


def _ensure_dir() -> None:
    os.makedirs(LEVELS_DIR, exist_ok=True)


def save_level(level_data: dict[str, Any], theme: str) -> str:
    """Persist a level to disk and return its ID."""
    _ensure_dir()
    level_id = str(uuid.uuid4())
    record = {
        "id": level_id,
        "theme": theme,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **level_data,
    }
    path = os.path.join(LEVELS_DIR, f"{level_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    logger.info("Saved level %s to %s", level_id, path)
    return level_id


def list_levels() -> list[dict[str, Any]]:
    """Return summary metadata for all saved levels, newest first."""
    _ensure_dir()
    summaries: list[dict[str, Any]] = []
    for filename in os.listdir(LEVELS_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(LEVELS_DIR, filename)
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
            summaries.append(
                {
                    "id": record.get("id", filename[:-5]),
                    "title": record.get("title", "Untitled"),
                    "theme": record.get("theme", ""),
                    "created_at": record.get("created_at", ""),
                }
            )
        except Exception as exc:
            logger.warning("Could not read level file %s: %s", filename, exc)

    summaries.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return summaries


def get_level(level_id: str) -> dict[str, Any] | None:
    """Load a single level by ID. Returns None if not found."""
    _ensure_dir()
    # Sanitize to prevent path traversal
    safe_id = os.path.basename(level_id)
    path = os.path.join(LEVELS_DIR, f"{safe_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Could not load level %s: %s", level_id, exc)
        return None
