"""Route handler for /generate-level."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..ai.base import AIGenerator
from ..ai.dependencies import get_ai_generator

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
    windows: list[WindowConfig]
    sprite_urls: list[str]


@router.post("/generate-level", response_model=GenerateLevelResponse)
async def generate_level(
    request: GenerateLevelRequest,
    ai: Annotated[AIGenerator, Depends(get_ai_generator)],
) -> Any:
    """Generate a complete level: layout config, background image, and sprites."""
    try:
        config = await ai.generate_level_config(request.theme)
    except Exception as exc:
        logger.exception("Failed to generate level config")
        raise HTTPException(status_code=502, detail=f"AI config generation failed: {exc}") from exc

    background_url = ""
    sprite_urls: list[str] = []

    if request.generate_images:
        try:
            background_url = await ai.generate_background(request.theme)
        except Exception as exc:
            logger.warning("Background generation failed, using empty string: %s", exc)

        descriptions: list[str] = config.get("monster_descriptions", [])
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
        windows=[WindowConfig(**w) for w in config.get("windows", [])],
        sprite_urls=sprite_urls,
    )
