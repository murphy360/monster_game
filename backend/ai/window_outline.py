"""Deterministic chroma-key window outlining helpers."""

from __future__ import annotations

import base64
import colorsys
import io
from collections import deque
from typing import Any

import httpx
from PIL import Image

DEFAULT_KEY_COLOR = (0, 255, 0)
KEY_COLOR_CANDIDATES = (
    (0, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
    (255, 255, 0),
    (255, 95, 0),
    (0, 166, 255),
)
COLOR_TOLERANCE = 72
SOFT_COLOR_DISTANCE_TOLERANCE = 340
SOFT_HUE_TOLERANCE = 0.09
SOFT_MIN_SATURATION = 0.25
SOFT_MIN_VALUE = 0.20
MIN_COMPONENT_AREA = 500
MIN_COMPONENT_SIDE = 20
EDGE_COLOR_TOLERANCE = 96
EDGE_GROW_PASSES = 2
RENDER_MASK_DILATION_RADIUS = 2
WINDOW_BOX_PADDING = 6
WINDOW_DARK_FILL = (6, 16, 30, 255)


def _parse_key_color(key_color: str | tuple[int, int, int] | list[int] | None) -> tuple[int, int, int]:
    """Normalize hex or RGB key-color input to an RGB tuple."""
    if key_color is None:
        return DEFAULT_KEY_COLOR

    if isinstance(key_color, str):
        normalized = key_color.strip().lstrip("#")
        if len(normalized) != 6:
            raise ValueError(f"Invalid key color '{key_color}'")
        return tuple(int(normalized[index:index + 2], 16) for index in (0, 2, 4))

    if len(key_color) != 3:
        raise ValueError("Key color must have exactly three channels")

    return tuple(max(0, min(int(channel), 255)) for channel in key_color)


def _channel_delta(r: int, g: int, b: int, key_color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Return absolute RGB channel distance from the configured key color."""
    kr, kg, kb = key_color
    return abs(r - kr), abs(g - kg), abs(b - kb)


def _hue_distance(a: float, b: float) -> float:
    """Return the shortest cyclic distance between two HSV hue values."""
    return min(abs(a - b), 1.0 - abs(a - b))


def _is_soft_key_color_match(r: int, g: int, b: int, key_color: tuple[int, int, int]) -> bool:
    """Return True for shaded/anti-aliased variants of the configured key color."""
    key_h, key_s, _ = colorsys.rgb_to_hsv(
        key_color[0] / 255.0,
        key_color[1] / 255.0,
        key_color[2] / 255.0,
    )
    hue, saturation, value = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

    if saturation < SOFT_MIN_SATURATION or value < SOFT_MIN_VALUE:
        return False
    if _hue_distance(hue, key_h) > SOFT_HUE_TOLERANCE:
        return False
    if abs(saturation - key_s) > 0.55:
        return False

    dr, dg, db = _channel_delta(r, g, b, key_color)
    return dr + dg + db <= SOFT_COLOR_DISTANCE_TOLERANCE


def _is_key_color(r: int, g: int, b: int, key_color: tuple[int, int, int]) -> bool:
    """Return True when a pixel is close to the configured chroma-key color."""
    dr, dg, db = _channel_delta(r, g, b, key_color)
    if dr <= COLOR_TOLERANCE and dg <= COLOR_TOLERANCE and db <= COLOR_TOLERANCE:
        return True

    return _is_soft_key_color_match(r, g, b, key_color)


def _is_key_edge_color(r: int, g: int, b: int, key_color: tuple[int, int, int]) -> bool:
    """Return True for anti-aliased edge pixels that still match the key color closely."""
    dr, dg, db = _channel_delta(r, g, b, key_color)
    return dr + dg + db <= EDGE_COLOR_TOLERANCE * 2


def _grow_mask_into_key_edges(
    mask: bytearray,
    pixels: list[tuple[int, int, int, int]],
    width: int,
    height: int,
    key_color: tuple[int, int, int],
) -> None:
    """Dilate the keyed mask into neighboring key-colored edge pixels."""
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
            if _is_key_edge_color(r, g, b, key_color):
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


def _key_color_hex(key_color: tuple[int, int, int]) -> str:
    """Encode a key color tuple as #RRGGBB."""
    return "#%02X%02X%02X" % key_color


def _dilate_mask(mask: bytearray, width: int, height: int, radius: int = 3) -> None:
    """Dilate (expand) the mask to fill small gaps between adjacent regions.
    
    This fills in small black dividers (like window pane separators) so that
    multi-pane windows are detected as a single connected component.
    """
    additions: list[int] = []
    
    for idx in range(width * height):
        if mask[idx]:
            continue
        
        x = idx % width
        y = idx // width
        has_masked_neighbor = False
        
        # Check if this unmasked pixel is within 'radius' distance of a masked pixel
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx = x + dx
                ny = y + dy
                
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor_idx = ny * width + nx
                    if mask[neighbor_idx]:
                        has_masked_neighbor = True
                        break
            
            if has_masked_neighbor:
                break
        
        if has_masked_neighbor:
            additions.append(idx)
    
    # Apply all additions
    for idx in additions:
        mask[idx] = 1


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

        padded_min_x = max(0, min_x - WINDOW_BOX_PADDING)
        padded_min_y = max(0, min_y - WINDOW_BOX_PADDING)
        padded_max_x = min(width - 1, max_x + WINDOW_BOX_PADDING)
        padded_max_y = min(height - 1, max_y + WINDOW_BOX_PADDING)

        boxes.append(
            {
                "x": padded_min_x,
                "y": padded_min_y,
                "width": padded_max_x - padded_min_x + 1,
                "height": padded_max_y - padded_min_y + 1,
            }
        )

    boxes.sort(key=lambda b: (b["y"], b["x"], b["width"], b["height"]))
    return boxes


def _score_windows(windows: list[dict[str, int]], width: int, height: int) -> float:
    """Heuristic score for picking the most plausible key-color interpretation."""
    if not windows:
        return 0.0

    image_area = width * height
    total_area = sum(win["width"] * win["height"] for win in windows)
    largest_area = max(win["width"] * win["height"] for win in windows)
    window_count = len(windows)

    if largest_area > image_area * 0.45:
        return 0.0
    if total_area < MIN_COMPONENT_AREA * 2:
        return 0.0

    count_factor = 1.0 if 4 <= window_count <= 30 else 0.55
    return total_area * count_factor - largest_area * 0.25


def _build_masks_for_key(
    pixels: list[tuple[int, int, int, int]],
    width: int,
    height: int,
    key_color: tuple[int, int, int],
) -> tuple[bytearray, bytearray, list[dict[str, int]]]:
    """Build base and cleanup masks plus detected windows for one key color."""
    mask = bytearray(width * height)
    for idx, (r, g, b, _) in enumerate(pixels):
        if _is_key_color(r, g, b, key_color):
            mask[idx] = 1

    _grow_mask_into_key_edges(mask, pixels, width, height, key_color)

    cleanup_mask = bytearray(mask)
    _dilate_mask(cleanup_mask, width, height, radius=RENDER_MASK_DILATION_RADIUS)

    dilated_mask = bytearray(mask)
    _dilate_mask(dilated_mask, width, height)
    windows = _connected_components(dilated_mask, width, height)

    return mask, cleanup_mask, windows


async def outline_windows_from_image(
    image_url: str,
    key_color: str | tuple[int, int, int] | list[int] | None = None,
) -> dict[str, Any]:
    """Build deterministic window boxes and visualization layers from chroma-key windows."""
    image_bytes, _ = await _decode_image_url(image_url)
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = base.size
    pixels = list(base.getdata())
    resolved_key_color = _parse_key_color(key_color)

    mask, cleanup_mask, windows = _build_masks_for_key(
        pixels,
        width,
        height,
        resolved_key_color,
    )

    best_score = _score_windows(windows, width, height)
    if best_score <= 0.0:
        for candidate in KEY_COLOR_CANDIDATES:
            if candidate == resolved_key_color:
                continue

            candidate_mask, candidate_cleanup_mask, candidate_windows = _build_masks_for_key(
                pixels,
                width,
                height,
                candidate,
            )
            candidate_score = _score_windows(candidate_windows, width, height)
            if candidate_score > best_score:
                resolved_key_color = candidate
                mask = candidate_mask
                cleanup_mask = candidate_cleanup_mask
                windows = candidate_windows
                best_score = candidate_score

    processed_pixels: list[tuple[int, int, int, int]] = []
    overlay_pixels: list[tuple[int, int, int, int]] = []
    mask_pixels: list[tuple[int, int, int, int]] = []

    for idx, (r, g, b, a) in enumerate(pixels):
        if cleanup_mask[idx]:
            processed_pixels.append(WINDOW_DARK_FILL)
            overlay_pixels.append((r, g, b, 0))
            mask_pixels.append((*resolved_key_color, 255))
        else:
            processed_pixels.append((r, g, b, a))
            overlay_pixels.append((r, g, b, a))
            mask_pixels.append((0, 0, 0, 255))

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
        "window_key_color": _key_color_hex(resolved_key_color),
    }
