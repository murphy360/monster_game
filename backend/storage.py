"""Persistent level storage using JSON files."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

LEVELS_DIR = os.environ.get("LEVELS_DIR", "/data/levels")


def _ensure_dir() -> None:
    os.makedirs(LEVELS_DIR, exist_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_level_name_key(title: Any) -> str:
    raw = str(title or "").strip().lower()
    compact = re.sub(r"\s+", " ", raw)
    alnum = re.sub(r"[^a-z0-9]+", "-", compact)
    return alnum.strip("-") or "untitled"


def _record_name_key(record: dict[str, Any]) -> str:
    explicit = str(record.get("level_name_key") or "").strip().lower()
    if explicit:
        return explicit
    return _normalize_level_name_key(record.get("title", "Untitled"))


def _record_sort_key(record: dict[str, Any]) -> tuple[str, int]:
    timestamp = str(record.get("updated_at") or record.get("created_at") or "")
    version = int(record.get("version") or 0)
    return timestamp, version


def _load_level_records() -> list[tuple[str, dict[str, Any]]]:
    _ensure_dir()
    records: list[tuple[str, dict[str, Any]]] = []
    for filename in os.listdir(LEVELS_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(LEVELS_DIR, filename)
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
            if isinstance(record, dict):
                records.append((path, record))
        except Exception as exc:
            logger.warning("Could not read level file %s: %s", filename, exc)
    return records


def _write_record(path: str, record: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)


def _set_current_for_name(level_name_key: str, current_level_id: str) -> None:
    for path, record in _load_level_records():
        if _record_name_key(record) != level_name_key:
            continue
        record_id = str(record.get("id") or "")
        should_be_current = record_id == current_level_id
        if bool(record.get("is_current")) == should_be_current:
            continue
        record["is_current"] = should_be_current
        if should_be_current and not record.get("updated_at"):
            record["updated_at"] = _utc_now_iso()
        _write_record(path, record)


def save_level(level_data: dict[str, Any], theme: str) -> str:
    """Persist a level to disk and return its ID."""
    _ensure_dir()
    now_iso = _utc_now_iso()
    title = str(level_data.get("title") or "Untitled").strip() or "Untitled"
    level_name_key = _normalize_level_name_key(title)

    existing_versions = []
    for _, record in _load_level_records():
        if _record_name_key(record) != level_name_key:
            continue
        existing_versions.append(int(record.get("version") or 0))

    level_id = str(uuid.uuid4())
    record = {
        "id": level_id,
        "theme": theme,
        "created_at": now_iso,
        "updated_at": now_iso,
        "title": title,
        "level_name_key": level_name_key,
        "version": (max(existing_versions) + 1) if existing_versions else 1,
        "is_current": True,
        **level_data,
    }
    path = os.path.join(LEVELS_DIR, f"{level_id}.json")
    _write_record(path, record)
    _set_current_for_name(level_name_key, level_id)
    logger.info("Saved level %s to %s", level_id, path)
    return level_id


def list_levels() -> list[dict[str, Any]]:
    """Return one current summary per level name, newest first."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for _, record in _load_level_records():
        groups.setdefault(_record_name_key(record), []).append(record)

    summaries: list[dict[str, Any]] = []
    for records in groups.values():
        current_records = [r for r in records if bool(r.get("is_current"))]
        chosen = max(current_records or records, key=_record_sort_key)
        summaries.append(
            {
                "id": chosen.get("id", ""),
                "title": chosen.get("title", "Untitled"),
                "theme": chosen.get("theme", ""),
                "created_at": chosen.get("created_at", ""),
                "updated_at": chosen.get("updated_at", chosen.get("created_at", "")),
                "version": int(chosen.get("version") or 1),
                "is_current": True,
                "versions_count": len(records),
            }
        )

    summaries.sort(key=lambda s: str(s.get("updated_at") or s.get("created_at") or ""), reverse=True)
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


def update_level(level_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update an existing level JSON record and return the saved record."""
    _ensure_dir()
    safe_id = os.path.basename(level_id)
    path = os.path.join(LEVELS_DIR, f"{safe_id}.json")
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)

        previous_name_key = _record_name_key(record)
        record.update(updates)
        title = str(record.get("title") or "Untitled").strip() or "Untitled"
        level_name_key = _normalize_level_name_key(title)
        record["title"] = title
        record["level_name_key"] = level_name_key
        record["version"] = int(record.get("version") or 1)
        record["updated_at"] = _utc_now_iso()
        record["is_current"] = True

        _write_record(path, record)
        _set_current_for_name(level_name_key, str(record.get("id") or safe_id))

        # If title/name changed, ensure the previous family still has a current record.
        if previous_name_key != level_name_key:
            candidates = [
                rec for _, rec in _load_level_records()
                if _record_name_key(rec) == previous_name_key
            ]
            if candidates:
                newest = max(candidates, key=_record_sort_key)
                _set_current_for_name(previous_name_key, str(newest.get("id") or ""))

        return record
    except Exception as exc:
        logger.warning("Could not update level %s: %s", level_id, exc)
        return None


def delete_level(level_id: str) -> bool:
    """Delete a saved level JSON record."""
    _ensure_dir()
    safe_id = os.path.basename(level_id)
    path = os.path.join(LEVELS_DIR, f"{safe_id}.json")
    if not os.path.isfile(path):
        return False

    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)

        level_name_key = _record_name_key(record)
        was_current = bool(record.get("is_current"))
        os.remove(path)

        if was_current:
            remaining = [
                other for _, other in _load_level_records()
                if _record_name_key(other) == level_name_key
            ]
            if remaining:
                newest = max(remaining, key=_record_sort_key)
                _set_current_for_name(level_name_key, str(newest.get("id") or ""))

        return True
    except Exception as exc:
        logger.warning("Could not delete level %s: %s", level_id, exc)
        return False
