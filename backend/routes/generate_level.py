"""Route handler for /generate-level."""

from __future__ import annotations

import asyncio
import json
import logging
import time
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
    making_sausage: bool = False


class WindowConfig(BaseModel):
    id: int
    x: int
    y: int
    width: int
    height: int


class MonsterMeta(BaseModel):
    name: str
    flavor: str = ""


class GenerateLevelResponse(BaseModel):
    title: str
    original_background_url: str | None = None
    cropped_background_url: str | None = None
    background_url: str
    overlay_url: str
    window_key_color: str | None = None
    boundary_color: str | None = None
    candidate_key_colors: list[str] = []
    color_decision: dict[str, Any] | None = None
    windows: list[WindowConfig]
    sprite_urls: list[str]
    monsters_meta: list[MonsterMeta] = []
    board_width: int
    board_height: int


BOARD_WIDTH = 1280
BOARD_HEIGHT = 720
BACKGROUND_STALL_TIMEOUT_SECONDS = 20.0


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


def _fallback_monster_name(description: str, index: int) -> str:
    """Create a readable monster name when the model omits one."""
    stop_words = {
        "a", "an", "and", "cartoon", "friendly", "from", "in", "monster",
        "of", "peeking", "the", "through", "tiny", "with",
    }
    words = [
        word.capitalize()
        for word in description.replace("-", " ").split()
        if word.isalpha() and word.lower() not in stop_words
    ]
    candidate = " ".join(words[:3]).strip()
    return candidate or f"Monster {index + 1}"


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
        "window_key_color": "#A7EF46",
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
        generation_warnings: list[str] = []

        # ── Step 1: get level config (fast text call) ────────────────────
        try:
            config = await ai.generate_level_config(request.theme)
        except Exception as exc:
            logger.warning("AI config generation failed; using fallback: %s", exc)
            generation_warnings.append("AI text generation failed; using fallback monsters.")
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

        # For "Making Sausage" mode, reduce to 1 sprite for faster testing
        if request.making_sausage:
            descriptions = descriptions[:1]
            names = names[:1]
            flavors = flavors[:1]

        # Pad names/flavors to exactly match the final sprite count.
        while len(names) < len(descriptions):
            names.append(_fallback_monster_name(descriptions[len(names)], len(names)))
        while len(flavors) < len(descriptions):
            flavors.append("")
        names = names[: len(descriptions)]
        flavors = flavors[: len(descriptions)]

        monsters_meta = [
            {"name": n, "flavor": f} for n, f in zip(names, flavors)
        ]

        yield f"event: sprite_count\ndata: {json.dumps({'count': len(descriptions), 'monsters': monsters_meta})}\n\n"

        # ── Step 2: kick off background generation concurrently ──────────
        background_attempt_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _on_background_attempt(payload: dict[str, Any]) -> None:
            """Collect per-attempt background images from the AI retry path."""
            await background_attempt_queue.put(payload)

        def _drain_background_attempt_events() -> list[str]:
            events: list[str] = []
            while True:
                try:
                    payload = background_attempt_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                if request.making_sausage and payload.get("url"):
                    events.append(
                        f"event: background_image\ndata: {json.dumps(payload)}\n\n"
                    )
                events.append(
                    "event: background_attempt\ndata: "
                    + json.dumps(
                        {
                            "attempt": payload.get("attempt", 0),
                            "max_attempts": payload.get("max_attempts", 0),
                            "status": payload.get("status", ""),
                        }
                    )
                    + "\n\n"
                )
            return events

        async def _gen_background() -> dict[str, Any]:
            try:
                return await ai.generate_background(
                    request.theme,
                    on_attempt=_on_background_attempt,
                )
            except Exception as exc:
                logger.warning("Background generation failed: %s", exc)
                generation_warnings.append("Background generation failed.")
                return {"image_url": "", "window_key_color": "#A7EF46"}

        bg_task: asyncio.Task[dict[str, str]] | None = None
        if request.generate_images:
            bg_task = asyncio.create_task(_gen_background())

        # ── Step 3: generate sprites concurrently, stream each as it finishes ─────────
        if request.generate_images:
            async def _gen_sprite_with_index(idx: int, desc: str) -> tuple[int, str]:
                try:
                    url = await ai.generate_sprite(desc)
                except Exception as exc:
                    logger.warning("Sprite generation failed for '%s': %s", desc, exc)
                    generation_warnings.append("One or more monster sprites failed to generate.")
                    url = ""
                return idx, url

            # Generate all sprites concurrently
            sprite_tasks = [_gen_sprite_with_index(i, desc) for i, desc in enumerate(descriptions)]
            for coro in asyncio.as_completed(sprite_tasks):
                idx, url = await coro
                # Ensure list is long enough
                while len(sprite_urls) <= idx:
                    sprite_urls.append("")
                sprite_urls[idx] = url
                yield f"event: sprite\ndata: {json.dumps({'index': idx, 'url': url})}\n\n"
                for event_message in _drain_background_attempt_events():
                    yield event_message

        # After sprites, keep streaming background attempts until background task finishes.
        latest_background_attempt: dict[str, Any] | None = None
        last_background_attempt_at = time.monotonic()

        if bg_task is not None:
            while not bg_task.done():
                try:
                    payload = await asyncio.wait_for(background_attempt_queue.get(), timeout=0.25)
                    latest_background_attempt = payload
                    last_background_attempt_at = time.monotonic()
                    if request.making_sausage and payload.get("url"):
                        yield f"event: background_image\ndata: {json.dumps(payload)}\n\n"
                    yield (
                        "event: background_attempt\ndata: "
                        + json.dumps(
                            {
                                "attempt": payload.get("attempt", 0),
                                "max_attempts": payload.get("max_attempts", 0),
                                "status": payload.get("status", ""),
                            }
                        )
                        + "\n\n"
                    )
                except asyncio.TimeoutError:
                    # If we already received at least one background attempt image,
                    # do not hang forever waiting for downstream validation steps.
                    if (
                        latest_background_attempt
                        and (time.monotonic() - last_background_attempt_at) >= BACKGROUND_STALL_TIMEOUT_SECONDS
                    ):
                        logger.warning(
                            "Background task stalled after attempt image for %.1fs; finalizing with last attempt",
                            BACKGROUND_STALL_TIMEOUT_SECONDS,
                        )
                        bg_task.cancel()
                        break
                    continue

            for event_message in _drain_background_attempt_events():
                yield event_message

        # ── Step 4: await background + outline windows ───────────────────
        original_background_url = ""
        cropped_background_url = ""
        background_url = ""
        overlay_url = ""
        window_key_color = str(config.get("window_key_color") or "#A7EF46")
        boundary_color = ""
        candidate_key_colors: list[str] = []
        color_decision: dict[str, Any] | None = None
        windows: list[dict[str, int]] = []

        if bg_task is not None:
            generated_background: dict[str, Any]
            if bg_task.done() and not bg_task.cancelled():
                generated_background = await bg_task
            else:
                # Use last streamed background attempt so we can still finalize/save a reviewable level.
                generated_background = {
                    "image_url": (latest_background_attempt or {}).get("url", ""),
                    "window_key_color": (latest_background_attempt or {}).get("window_key_color", "#A7EF46"),
                    "color_decision": (latest_background_attempt or {}).get("color_decision", {}),
                }
                generation_warnings.append("Background validation timed out; finalized from latest attempt image.")

            background_url = generated_background.get("image_url", "")
            original_background_url = background_url
            window_key_color = str(generated_background.get("window_key_color") or window_key_color)
            raw_decision = generated_background.get("color_decision")
            
            if isinstance(raw_decision, dict):
                color_decision = dict(raw_decision)
                final_attempt = color_decision.get("attempt", 1)
                # Emit an event for the final attempt
                bg_image_status = "success" if background_url else "failed"
                yield f"event: background_attempt\ndata: {json.dumps({'attempt': final_attempt, 'status': bg_image_status})}\n\n"

        if background_url:
            try:
                outlined = await outline_windows_from_image(background_url, window_key_color)
                cropped_background_url = str(outlined.get("cropped_background_url") or "")
                background_url = outlined.get("processed_background_url", background_url)
                overlay_url = outlined.get("overlay_url", "")
                window_key_color = str(outlined.get("window_key_color") or window_key_color)
                boundary_color = str(outlined.get("boundary_color") or "")
                candidate_key_colors = [
                    str(color).upper()
                    for color in (outlined.get("candidate_key_colors") or [])
                    if isinstance(color, str) and color.strip()
                ]
                if color_decision is None:
                    color_decision = {}
                color_decision["final_mask_removal_color"] = window_key_color
                if boundary_color:
                    color_decision["boundary_color"] = boundary_color
                if candidate_key_colors:
                    color_decision["candidate_key_colors"] = candidate_key_colors
                board_width = _as_int(outlined.get("board_width"), BOARD_WIDTH) or BOARD_WIDTH
                board_height = _as_int(outlined.get("board_height"), BOARD_HEIGHT) or BOARD_HEIGHT
                windows = _normalize_windows(
                    outlined.get("windows", []),
                    board_width,
                    board_height,
                )
            except Exception as exc:
                logger.warning("Window outlining failed; falling back to config windows: %s", exc)
                outlined = {}

        if not windows:
            if isinstance(color_decision, dict):
                selected_windows = color_decision.get("selected_windows", [])
                if isinstance(selected_windows, list) and selected_windows:
                    windows = _normalize_windows(selected_windows, board_width, board_height)

        if not windows:
            windows = _normalize_windows(config.get("windows", []), board_width, board_height)

        title = config.get("title", request.theme)
        sprite_success_count = sum(1 for url in sprite_urls if url)
        warning_message = ""
        if generation_warnings:
            unique_warnings = sorted(set(generation_warnings))
            warning_message = " ".join(unique_warnings)

        # Check if we're using fallback/config windows instead of auto-detected ones
        manual_selection_required = bool(background_url and not outlined.get("windows", []))

        # ── Step 5: emit layout so client can show the game board ────────
        layout_payload = {
            "title": title,
            "original_background_url": original_background_url,
            "cropped_background_url": cropped_background_url,
            "background_url": background_url,
            "overlay_url": overlay_url,
            "window_key_color": window_key_color,
            "boundary_color": boundary_color or None,
            "candidate_key_colors": candidate_key_colors,
            "color_decision": color_decision,
            "windows": windows,
            "board_width": board_width,
            "board_height": board_height,
            "sprite_success_count": sprite_success_count,
            "generation_warning": warning_message,
            "manual_selection_required": manual_selection_required,
        }
        yield f"event: layout\ndata: {json.dumps(layout_payload)}\n\n"

        # ── Step 6: save then signal done ────────────────────────────────
        full_response = GenerateLevelResponse(
            title=title,
            original_background_url=original_background_url,
            cropped_background_url=cropped_background_url or None,
            background_url=background_url,
            overlay_url=overlay_url,
            window_key_color=window_key_color,
            boundary_color=boundary_color or None,
            candidate_key_colors=candidate_key_colors,
            color_decision=color_decision,
            windows=[WindowConfig(**w) for w in windows],
            sprite_urls=sprite_urls,
            monsters_meta=[MonsterMeta(**m) for m in monsters_meta],
            board_width=board_width,
            board_height=board_height,
        )
        try:
            save_level(full_response.model_dump(), request.theme)
        except Exception as exc:
            logger.warning("Failed to save level: %s", exc)

        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
