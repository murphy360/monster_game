"""Deterministic chroma-key window outlining helpers."""

from __future__ import annotations

import base64
import io
from collections import deque
from typing import Any

import httpx
from PIL import Image

KEY_COLOR = (0, 255, 0)
COLOR_TOLERANCE = 72
MIN_COMPONENT_AREA = 500
MIN_COMPONENT_SIDE = 20
EDGE_GROW_PASSES = 2
WINDOW_DARK_FILL = (6, 16, 30, 255)


def _is_key_color(r: int, g: int, b: int) -> bool:
    """Return True when a pixel is close to the configured chroma-key green."""
    kr, kg, kb = KEY_COLOR
    close_to_key = (
        abs(r - kr) <= COLOR_TOLERANCE
        and abs(g - kg) <= COLOR_TOLERANCE
        and abs(b - kb) <= COLOR_TOLERANCE
    )

    # Generated images often anti-alias key windows, so accept bright,
    # clearly green-dominant pixels even when not near exact #00FF00.
    dominant_green = g >= 150 and g >= r + 35 and g >= b + 35
    return close_to_key or dominant_green


def _is_greenish_edge(r: int, g: int, b: int) -> bool:
    """Return True for softer green pixels that appear on anti-aliased edges."""
    return g >= 80 and g >= r + 16 and g >= b + 16


def _grow_mask_into_greenish_edges(
    mask: bytearray,
    pixels: list[tuple[int, int, int, int]],
    width: int,
    height: int,
) -> None:
    """Dilate the keyed mask into neighboring greenish pixels to reduce green halos."""
    for _ in range(EDGE_GROW_PASSES):
        additions: list[int] = []
        for idx in range(width * height):
            if mask[idx]:
                continue

            x = idx % width
            y = idx // width
            has_masked_neighbor = False

            if x > 0 and mask[idx - 1]:
                has_masked_neighbor = True
            elif x < width - 1 and mask[idx + 1]:
                has_masked_neighbor = True
            elif y > 0 and mask[idx - width]:
                has_masked_neighbor = True
            elif y < height - 1 and mask[idx + width]:
                has_masked_neighbor = True

            if not has_masked_neighbor:
                continue

            r, g, b, _ = pixels[idx]
            if _is_greenish_edge(r, g, b):
                additions.append(idx)

        if not additions:
            return

        for idx in additions:
            mask[idx] = 1


async def _decode_image_url(image_url: str) -> tuple[bytes, str]:
    """Decode URL/data-URI image content into bytes and mime type."""
    if image_url.startswith("http"):
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url)
            response.raise_for_status()
            mime = response.headers.get("content-type", "image/png").split(";")[0]
            return response.content, mime

    header, encoded = image_url.split(",", 1)
    mime = header.split(":")[1].split(";")[0]
    return base64.b64decode(encoded), mime


def _encode_png_data_uri(image: Image.Image) -> str:
    """Encode a PIL image to PNG data URI."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/png;base64," + b64


def _connected_components(mask: bytearray, width: int, height: int) -> list[dict[str, int]]:
    """Return bounding boxes for connected mask regions."""
    visited = bytearray(width * height)
    boxes: list[dict[str, int]] = []

    for start in range(width * height):
        if not mask[start] or visited[start]:
            continue

        queue: deque[int] = deque([start])
        visited[start] = 1

        min_x = width
        min_y = height
        max_x = -1
        max_y = -1
        area = 0

        while queue:
            idx = queue.popleft()
            x = idx % width
            y = idx // width
            area += 1

            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y

            if x > 0:
                left = idx - 1
                if mask[left] and not visited[left]:
                    visited[left] = 1
                    queue.append(left)
            if x < width - 1:
                right = idx + 1
                if mask[right] and not visited[right]:
                    visited[right] = 1
                    queue.append(right)
            if y > 0:
                up = idx - width
                if mask[up] and not visited[up]:
                    visited[up] = 1
                    queue.append(up)
            if y < height - 1:
                down = idx + width
                if mask[down] and not visited[down]:
                    visited[down] = 1
                    queue.append(down)

        component_width = max_x - min_x + 1
        component_height = max_y - min_y + 1
        if area < MIN_COMPONENT_AREA:
            continue
        if component_width < MIN_COMPONENT_SIDE or component_height < MIN_COMPONENT_SIDE:
            continue

        boxes.append(
            {
                "x": min_x,
                "y": min_y,
                "width": component_width,
                "height": component_height,
            }
        )

    boxes.sort(key=lambda b: (b["y"], b["x"], b["width"], b["height"]))
    return boxes


async def outline_windows_from_image(image_url: str) -> dict[str, Any]:
    """Build deterministic window boxes and visualization layers from chroma-key windows."""
    image_bytes, _ = await _decode_image_url(image_url)
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = base.size
    pixels = list(base.getdata())

    mask = bytearray(width * height)
    for idx, (r, g, b, _) in enumerate(pixels):
        if _is_key_color(r, g, b):
            mask[idx] = 1

    _grow_mask_into_greenish_edges(mask, pixels, width, height)

    processed_pixels: list[tuple[int, int, int, int]] = []
    overlay_pixels: list[tuple[int, int, int, int]] = []
    mask_pixels: list[tuple[int, int, int, int]] = []

    for idx, (r, g, b, a) in enumerate(pixels):
        if mask[idx]:
            processed_pixels.append(WINDOW_DARK_FILL)
            overlay_pixels.append((r, g, b, 0))
            mask_pixels.append((0, 255, 0, 255))
        else:
            processed_pixels.append((r, g, b, a))
            overlay_pixels.append((r, g, b, a))
            mask_pixels.append((0, 0, 0, 255))

    windows = _connected_components(mask, width, height)

    processed = Image.new("RGBA", (width, height))
    processed.putdata(processed_pixels)

    overlay = Image.new("RGBA", (width, height))
    overlay.putdata(overlay_pixels)

    mask_image = Image.new("RGBA", (width, height))
    mask_image.putdata(mask_pixels)

    return {
        "windows": windows,
        "processed_background_url": _encode_png_data_uri(processed),
        "overlay_url": _encode_png_data_uri(overlay),
        "mask_url": _encode_png_data_uri(mask_image),
        "board_width": width,
        "board_height": height,
    }
