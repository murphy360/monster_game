"""Route handler for /serve-assets."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..ai.base import AIGenerator
from ..ai.dependencies import get_ai_generator
from ..ai.window_outline import outline_windows_from_image

router = APIRouter()
logger = logging.getLogger(__name__)


class ServeAssetsRequest(BaseModel):
    image_url: str
    window_key_color: str | None = None
    character_descriptions: list[str] = []


class ServeAssetsResponse(BaseModel):
    windows: list[dict[str, Any]]
    sprite_urls: list[str]
    window_key_color: str | None = None
    processed_background_url: str | None = None
    overlay_url: str | None = None
    mask_url: str | None = None
    board_width: int | None = None
    board_height: int | None = None
    method: str = "chroma-key"


@router.post("/serve-assets", response_model=ServeAssetsResponse)
async def serve_assets(
    request: ServeAssetsRequest,
    ai: Annotated[AIGenerator, Depends(get_ai_generator)],
) -> Any:
    """Extract window bounding boxes from a background image and optionally
    generate sprite images for each provided character description.
    """
    try:
        outlined = await outline_windows_from_image(request.image_url, request.window_key_color)
        windows = outlined.get("windows", [])
    except Exception as exc:
        logger.exception("Bounding box extraction failed")
        raise HTTPException(status_code=502, detail=f"Bounding box extraction failed: {exc}") from exc

    sprite_urls: list[str] = []
    for desc in request.character_descriptions:
        try:
            url = await ai.generate_sprite(desc)
        except Exception as exc:
            logger.warning("Sprite generation failed for '%s': %s", desc, exc)
            url = ""
        sprite_urls.append(url)

    return ServeAssetsResponse(
        windows=windows,
        sprite_urls=sprite_urls,
        window_key_color=outlined.get("window_key_color"),
        processed_background_url=outlined.get("processed_background_url"),
        overlay_url=outlined.get("overlay_url"),
        mask_url=outlined.get("mask_url"),
        board_width=outlined.get("board_width"),
        board_height=outlined.get("board_height"),
    )
