"""Route handler for /generate-level."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..ai.base import AIGenerator
from ..ai.dependencies import get_ai_generator
from ..ai.window_outline import outline_windows_from_image

router = APIRouter()
logger = logging.getLogger(__name__)


class GenerateLevelRequest(BaseModel):
    theme: str = "haunted house"
    generate_images: bool = True


class WindowConfig(BaseModel):
    id: int
    x: int
    y: int
    width: int
    height: int


class GenerateLevelResponse(BaseModel):
    title: str
    background_url: str
    overlay_url: str
    windows: list[WindowConfig]
    sprite_urls: list[str]
    board_width: int
    board_height: int


BOARD_WIDTH = 1280
BOARD_HEIGHT = 720


def _as_int(value: Any, default: int = 0) -> int:
    """Safely convert arbitrary values to ints."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_windows(
    windows: list[dict[str, Any]],
    board_width: int,
    board_height: int,
) -> list[dict[str, int]]:
    """Normalize detected windows into stable id/x/y/width/height records."""
    normalized_raw: list[dict[str, int]] = []

    for raw in windows:
        width = max(1, min(_as_int(raw.get("width"), 0), board_width))
        height = max(1, min(_as_int(raw.get("height"), 0), board_height))
        x = _as_int(raw.get("x"), 0)
        y = _as_int(raw.get("y"), 0)

        # Clamp origin so each box remains fully inside board bounds.
        x = max(0, min(x, max(0, board_width - width)))
        y = max(0, min(y, max(0, board_height - height)))

        normalized_raw.append(
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
        )

    # Keep ordering deterministic across responses so id assignment is stable.
    normalized_raw.sort(key=lambda win: (win["y"], win["x"], win["width"], win["height"]))

    normalized: list[dict[str, int]] = []
    for idx, win in enumerate(normalized_raw):
        normalized.append(
            {
                "id": idx + 1,
                "x": win["x"],
                "y": win["y"],
                "width": win["width"],
                "height": win["height"],
            }
        )

    return normalized


def _align_monster_descriptions(
    descriptions: list[str],
    window_count: int,
) -> list[str]:
    """Ensure exactly one description per detected window."""
    clean = [d for d in descriptions if isinstance(d, str) and d.strip()]
    if window_count <= 0:
        return []

    if len(clean) >= window_count:
        return clean[:window_count]

    fallback = clean[-1] if clean else "friendly cartoon monster peeking from a window"
    return clean + [fallback] * (window_count - len(clean))


def _fallback_level_config(theme: str) -> dict[str, Any]:
    """Return a deterministic level config for local testing when AI fails."""
    windows: list[dict[str, int]] = [
        {"id": 1, "x": 120, "y": 130, "width": 140, "height": 170},
        {"id": 2, "x": 330, "y": 130, "width": 140, "height": 170},
        {"id": 3, "x": 540, "y": 130, "width": 140, "height": 170},
        {"id": 4, "x": 750, "y": 130, "width": 140, "height": 170},
        {"id": 5, "x": 120, "y": 360, "width": 140, "height": 170},
        {"id": 6, "x": 330, "y": 360, "width": 140, "height": 170},
        {"id": 7, "x": 540, "y": 360, "width": 140, "height": 170},
        {"id": 8, "x": 750, "y": 360, "width": 140, "height": 170},
    ]
    return {
        "title": f"{theme.title()} (Local Test)",
        "windows": windows,
        "monster_descriptions": [
            "friendly green blob monster with tiny horns" for _ in windows
        ],
    }


@router.post("/generate-level", response_model=GenerateLevelResponse)
async def generate_level(
    request: GenerateLevelRequest,
    ai: Annotated[AIGenerator, Depends(get_ai_generator)],
) -> Any:
    """Generate a complete level: layout config, background image, and sprites."""
    board_width = BOARD_WIDTH
    board_height = BOARD_HEIGHT

    background_url = ""
    overlay_url = ""
    windows: list[dict[str, int]] = []
    config: dict[str, Any]
    sprite_urls: list[str] = []

    if request.generate_images:
        try:
            background_url = await ai.generate_background(request.theme)
        except Exception as exc:
            logger.warning("Background generation failed, using empty string: %s", exc)

        if background_url:
            try:
                outlined = await outline_windows_from_image(background_url)
                background_url = outlined.get("processed_background_url", background_url)
                overlay_url = outlined.get("overlay_url", "")
                board_width = _as_int(outlined.get("board_width"), BOARD_WIDTH) or BOARD_WIDTH
                board_height = _as_int(outlined.get("board_height"), BOARD_HEIGHT) or BOARD_HEIGHT
                windows = _normalize_windows(
                    outlined.get("windows", []),
                    board_width,
                    board_height,
                )
            except Exception as exc:
                logger.warning(
                    "Deterministic window outlining failed; falling back to config windows: %s",
                    exc,
                )

    try:
        config = await ai.generate_level_config(request.theme)
    except Exception as exc:
        logger.warning(
            "AI config generation failed; using local fallback level: %s", exc
        )
        config = _fallback_level_config(request.theme)

    # Source of truth is the generated background extraction when available.
    if not windows:
        windows = _normalize_windows(config.get("windows", []), board_width, board_height)

    if request.generate_images:
        descriptions = _align_monster_descriptions(
            config.get("monster_descriptions", []),
            len(windows),
        )
        for desc in descriptions:
            try:
                url = await ai.generate_sprite(desc)
            except Exception as exc:
                logger.warning("Sprite generation failed for '%s': %s", desc, exc)
                url = ""
            sprite_urls.append(url)

    return GenerateLevelResponse(
        title=config.get("title", request.theme),
        background_url=background_url,
        overlay_url=overlay_url,
        windows=[WindowConfig(**w) for w in windows],
        sprite_urls=sprite_urls,
        board_width=board_width,
        board_height=board_height,
    )
