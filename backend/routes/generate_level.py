"""Route handler for /generate-level."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any, AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..ai.base import AIGenerator
from ..ai.dependencies import get_ai_generator
from ..ai.window_outline import outline_windows_from_image
from ..storage import save_level

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
    window_key_color: str | None = None
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
        "window_key_color": "#00FF00",
        "windows": windows,
        "monster_descriptions": [
            "friendly green blob monster with tiny horns" for _ in windows
        ],
    }


@router.post("/generate-level")
async def generate_level(
    request: GenerateLevelRequest,
    ai: Annotated[AIGenerator, Depends(get_ai_generator)],
) -> StreamingResponse:
    """Generate a level and stream it via SSE.

    Events emitted in order:
      - ``sprite_count`` – how many sprites will be generated.
      - ``sprite``       – one event per monster as each finishes, ``{index, url}``.
      - ``layout``       – board geometry + background/overlay URLs (background
                           was generated concurrently with sprites).
      - ``done``         – stream complete.
    """

    async def _event_stream() -> AsyncGenerator[str, None]:
        board_width = BOARD_WIDTH
        board_height = BOARD_HEIGHT
        sprite_urls: list[str] = []

        # ── Step 1: get level config (fast text call) ────────────────────
        try:
            config = await ai.generate_level_config(request.theme)
        except Exception as exc:
            logger.warning("AI config generation failed; using fallback: %s", exc)
            config = _fallback_level_config(request.theme)

        MIN_MONSTERS = 6
        MAX_MONSTERS = 8

        def _extract_monsters(
            cfg: dict[str, Any],
        ) -> tuple[list[str], list[str], list[str]]:
            descriptions_local = [
                d for d in cfg.get("monster_descriptions", [])
                if isinstance(d, str) and d.strip()
            ]
            names_local = [
                n for n in cfg.get("monster_names", [])
                if isinstance(n, str) and n.strip()
            ]
            flavors_local = [
                f for f in cfg.get("monster_flavor", [])
                if isinstance(f, str)
            ]
            return descriptions_local, names_local, flavors_local

        descriptions, names, flavors = _extract_monsters(config)

        # If the first batch is short, request one more batch before background generation.
        if len(descriptions) < MIN_MONSTERS:
            logger.info(
                "Only got %d monsters (min=%d); requesting a second batch.",
                len(descriptions),
                MIN_MONSTERS,
            )
            try:
                extra_config = await ai.generate_level_config(request.theme)
                extra_descriptions, extra_names, extra_flavors = _extract_monsters(extra_config)
                descriptions.extend(extra_descriptions)
                names.extend(extra_names)
                flavors.extend(extra_flavors)
            except Exception as exc:
                logger.warning("Second monster batch failed: %s", exc)

        # Keep variety but cap the upper bound.
        descriptions = descriptions[:MAX_MONSTERS]
        names = names[:MAX_MONSTERS]
        flavors = flavors[:MAX_MONSTERS]

        # If still short, pad to minimum so gameplay can continue.
        fallback_desc = "friendly cartoon monster peeking from a window"
        if len(descriptions) < MIN_MONSTERS:
            last = descriptions[-1] if descriptions else fallback_desc
            descriptions.extend([last] * (MIN_MONSTERS - len(descriptions)))

        # Pad names/flavors to exactly match the final sprite count.
        while len(names) < len(descriptions):
            names.append(f"Monster {len(names) + 1}")
        while len(flavors) < len(descriptions):
            flavors.append("")
        names = names[: len(descriptions)]
        flavors = flavors[: len(descriptions)]

        monsters_meta = [
            {"name": n, "flavor": f} for n, f in zip(names, flavors)
        ]

        yield f"event: sprite_count\ndata: {json.dumps({'count': len(descriptions), 'monsters': monsters_meta})}\n\n"

        # ── Step 2: kick off background generation concurrently ──────────
        async def _gen_background() -> dict[str, str]:
            try:
                return await ai.generate_background(request.theme)
            except Exception as exc:
                logger.warning("Background generation failed: %s", exc)
                return {"image_url": "", "window_key_color": "#00FF00"}

        bg_task: asyncio.Task[str] | None = None
        if request.generate_images:
            bg_task = asyncio.create_task(_gen_background())

        # ── Step 3: generate sprites, stream each as it finishes ─────────
        if request.generate_images:
            for idx, desc in enumerate(descriptions):
                try:
                    url = await ai.generate_sprite(desc)
                except Exception as exc:
                    logger.warning("Sprite generation failed for '%s': %s", desc, exc)
                    url = ""
                sprite_urls.append(url)
                yield f"event: sprite\ndata: {json.dumps({'index': idx, 'url': url})}\n\n"

        # ── Step 4: await background + outline windows ───────────────────
        background_url = ""
        overlay_url = ""
        window_key_color = str(config.get("window_key_color") or "#00FF00")
        windows: list[dict[str, int]] = []

        if bg_task is not None:
            generated_background = await bg_task
            background_url = generated_background.get("image_url", "")
            window_key_color = str(generated_background.get("window_key_color") or window_key_color)

        if background_url:
            try:
                outlined = await outline_windows_from_image(background_url, window_key_color)
                background_url = outlined.get("processed_background_url", background_url)
                overlay_url = outlined.get("overlay_url", "")
                window_key_color = str(outlined.get("window_key_color") or window_key_color)
                board_width = _as_int(outlined.get("board_width"), BOARD_WIDTH) or BOARD_WIDTH
                board_height = _as_int(outlined.get("board_height"), BOARD_HEIGHT) or BOARD_HEIGHT
                windows = _normalize_windows(
                    outlined.get("windows", []),
                    board_width,
                    board_height,
                )
            except Exception as exc:
                logger.warning("Window outlining failed; falling back to config windows: %s", exc)

        if not windows:
            windows = _normalize_windows(config.get("windows", []), board_width, board_height)

        # Align sprite list to actual window count (pad with "" or trim)
        while len(sprite_urls) < len(windows):
            sprite_urls.append("")
        sprite_urls = sprite_urls[: len(windows)]

        title = config.get("title", request.theme)

        # ── Step 5: emit layout so client can show the game board ────────
        layout_payload = {
            "title": title,
            "background_url": background_url,
            "overlay_url": overlay_url,
            "window_key_color": window_key_color,
            "windows": windows,
            "board_width": board_width,
            "board_height": board_height,
        }
        yield f"event: layout\ndata: {json.dumps(layout_payload)}\n\n"

        # ── Step 6: save then signal done ────────────────────────────────
        full_response = GenerateLevelResponse(
            title=title,
            background_url=background_url,
            overlay_url=overlay_url,
            window_key_color=window_key_color,
            windows=[WindowConfig(**w) for w in windows],
            sprite_urls=sprite_urls,
            board_width=board_width,
            board_height=board_height,
        )
        try:
            save_level(full_response.model_dump(), request.theme)
        except Exception as exc:
            logger.warning("Failed to save level: %s", exc)

        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
