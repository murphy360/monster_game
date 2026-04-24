"""Route handler for /serve-assets."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..ai.base import AIGenerator
from ..ai.dependencies import get_ai_generator

router = APIRouter()
logger = logging.getLogger(__name__)


class ServeAssetsRequest(BaseModel):
    image_url: str
    character_descriptions: list[str] = []


class ServeAssetsResponse(BaseModel):
    windows: list[dict[str, Any]]
    sprite_urls: list[str]


@router.post("/serve-assets", response_model=ServeAssetsResponse)
async def serve_assets(
    request: ServeAssetsRequest,
    ai: Annotated[AIGenerator, Depends(get_ai_generator)],
) -> Any:
    """Extract window bounding boxes from a background image and optionally
    generate sprite images for each provided character description.
    """
    try:
        windows = await ai.extract_bounding_boxes(request.image_url)
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

    return ServeAssetsResponse(windows=windows, sprite_urls=sprite_urls)
