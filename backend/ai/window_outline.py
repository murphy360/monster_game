"""Deterministic chroma-key window outlining helpers."""

from __future__ import annotations

import base64
import io
from collections import deque
from typing import Any

import httpx
from PIL import Image

DEFAULT_KEY_COLOR = (167, 239, 70)
KEY_COLOR_CANDIDATES = (
    (167, 239, 70),
    (154, 46, 130),
    (255, 106, 19),
)
KEY_COLOR_TOLERANCE = 30
KEY_COLOR_DISTANCE_MAX = 140
MIN_COMPONENT_AREA = 500
MIN_COMPONENT_SIDE = 20
RENDER_MASK_DILATION_RADIUS = 2
WINDOW_BOX_PADDING = 6
SCORE_MIN_FILL_RATIO = 0.4
WINDOW_DARK_FILL = (6, 16, 30, 255)
BOUNDARY_SAMPLE_BAND = 12
BOUNDARY_COLOR_BUCKET_SIZE = 16
BOUNDARY_COLOR_TOLERANCE = 28
BOUNDARY_LINE_MATCH_RATIO = 0.93
BOUNDARY_MIN_CROP_PIXELS = 3
BOUNDARY_MAX_CROP_RATIO = 0.18


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


def _is_key_color(r: int, g: int, b: int, key_color: tuple[int, int, int]) -> bool:
    """Return True when a pixel matches key color while rejecting lookalike scene colors.

    We combine broad RGB-distance with key-specific dominance checks so that
    olive/pastel greens (or similar lookalikes for other keys) do not get
    treated as chroma mask pixels.
    """
    kr, kg, kb = key_color
    dr = abs(r - kr)
    dg = abs(g - kg)
    db = abs(b - kb)

    # Fast path for near-exact matches.
    if dr <= KEY_COLOR_TOLERANCE and dg <= KEY_COLOR_TOLERANCE and db <= KEY_COLOR_TOLERANCE:
        return True

    # Broad distance gate allows anti-aliased/compressed key areas.
    if (dr * dr + dg * dg + db * db) > (KEY_COLOR_DISTANCE_MAX * KEY_COLOR_DISTANCE_MAX):
        return False

    # Key-specific dominance rules to reject scene colors that are merely similar.
    if key_color in ((167, 239, 70), (0, 255, 0)):
        # True key green should strongly dominate red/blue channels.
        return g >= 120 and (g - r) >= 35 and (g - b) >= 35

    if key_color == (154, 46, 130):
        # Key fuchsia (#9A2E82) requires red/blue dominance over green.
        return r >= 95 and b >= 85 and abs(r - b) <= 95 and (min(r, b) - g) >= 25

    if key_color == (255, 106, 19):
        # Key orange needs high red, moderate green, and clearly low blue.
        return r >= 140 and g >= 55 and g <= 200 and b <= 110 and (r - g) >= 20 and (g - b) >= 15

    # Fallback for unexpected custom colors.
    return dr <= KEY_COLOR_TOLERANCE and dg <= KEY_COLOR_TOLERANCE and db <= KEY_COLOR_TOLERANCE


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


def _is_color_match(
    rgb: tuple[int, int, int],
    target: tuple[int, int, int],
    tolerance: int = BOUNDARY_COLOR_TOLERANCE,
) -> bool:
    """Return True when a color is within per-channel tolerance of target."""
    return all(abs(channel - reference) <= tolerance for channel, reference in zip(rgb, target))


def _estimate_boundary_color(image: Image.Image) -> tuple[int, int, int] | None:
    """Estimate dominant color in the outer border area, if one exists."""
    width, height = image.size
    if width < 8 or height < 8:
        return None

    sample_band = max(1, min(BOUNDARY_SAMPLE_BAND, width // 6, height // 6))
    pixels = image.load()

    buckets: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}

    def _add_sample(x: int, y: int) -> None:
        r, g, b, _ = pixels[x, y]
        key = (r // BOUNDARY_COLOR_BUCKET_SIZE, g // BOUNDARY_COLOR_BUCKET_SIZE, b // BOUNDARY_COLOR_BUCKET_SIZE)
        buckets.setdefault(key, []).append((r, g, b))

    for y in range(sample_band):
        for x in range(width):
            _add_sample(x, y)
            _add_sample(x, height - 1 - y)
    for x in range(sample_band):
        for y in range(height):
            _add_sample(x, y)
            _add_sample(width - 1 - x, y)

    if not buckets:
        return None

    dominant_bucket = max(buckets.items(), key=lambda entry: len(entry[1]))[1]
    if not dominant_bucket:
        return None

    count = len(dominant_bucket)
    avg_r = sum(pixel[0] for pixel in dominant_bucket) // count
    avg_g = sum(pixel[1] for pixel in dominant_bucket) // count
    avg_b = sum(pixel[2] for pixel in dominant_bucket) // count
    candidate = (avg_r, avg_g, avg_b)

    def _edge_match_ratio(edge: str) -> float:
        total = 0
        matched = 0
        if edge in ("top", "bottom"):
            y = 0 if edge == "top" else height - 1
            for x in range(width):
                total += 1
                r, g, b, _ = pixels[x, y]
                if _is_color_match((r, g, b), candidate):
                    matched += 1
        else:
            x = 0 if edge == "left" else width - 1
            for y in range(height):
                total += 1
                r, g, b, _ = pixels[x, y]
                if _is_color_match((r, g, b), candidate):
                    matched += 1
        return matched / max(1, total)

    ratios = [_edge_match_ratio(edge) for edge in ("top", "right", "bottom", "left")]
    if min(ratios) < 0.6:
        return None

    return candidate


def _measure_boundary_thickness(
    image: Image.Image,
    boundary_color: tuple[int, int, int],
) -> tuple[int, int, int, int]:
    """Measure how many solid-color boundary pixels exist on each edge."""
    width, height = image.size
    pixels = image.load()

    max_scan_x = max(1, int(width * BOUNDARY_MAX_CROP_RATIO))
    max_scan_y = max(1, int(height * BOUNDARY_MAX_CROP_RATIO))

    def _line_ratio_top(y: int) -> float:
        matched = 0
        for x in range(width):
            r, g, b, _ = pixels[x, y]
            if _is_color_match((r, g, b), boundary_color):
                matched += 1
        return matched / max(1, width)

    def _line_ratio_bottom(y: int) -> float:
        matched = 0
        yy = height - 1 - y
        for x in range(width):
            r, g, b, _ = pixels[x, yy]
            if _is_color_match((r, g, b), boundary_color):
                matched += 1
        return matched / max(1, width)

    def _line_ratio_left(x: int) -> float:
        matched = 0
        for y in range(height):
            r, g, b, _ = pixels[x, y]
            if _is_color_match((r, g, b), boundary_color):
                matched += 1
        return matched / max(1, height)

    def _line_ratio_right(x: int) -> float:
        matched = 0
        xx = width - 1 - x
        for y in range(height):
            r, g, b, _ = pixels[xx, y]
            if _is_color_match((r, g, b), boundary_color):
                matched += 1
        return matched / max(1, height)

    top = 0
    for y in range(max_scan_y):
        if _line_ratio_top(y) >= BOUNDARY_LINE_MATCH_RATIO:
            top += 1
        else:
            break

    bottom = 0
    for y in range(max_scan_y):
        if _line_ratio_bottom(y) >= BOUNDARY_LINE_MATCH_RATIO:
            bottom += 1
        else:
            break

    left = 0
    for x in range(max_scan_x):
        if _line_ratio_left(x) >= BOUNDARY_LINE_MATCH_RATIO:
            left += 1
        else:
            break

    right = 0
    for x in range(max_scan_x):
        if _line_ratio_right(x) >= BOUNDARY_LINE_MATCH_RATIO:
            right += 1
        else:
            break

    return left, top, right, bottom


def _crop_boundary(
    image: Image.Image,
    boundary_color: tuple[int, int, int] | None,
) -> tuple[Image.Image, dict[str, int], bool]:
    """Crop uniform image boundary and return cropped image plus crop box metadata."""
    if boundary_color is None:
        return image, {"left": 0, "top": 0, "right": 0, "bottom": 0}, False

    width, height = image.size
    left, top, right, bottom = _measure_boundary_thickness(image, boundary_color)
    minimum = min(left, top, right, bottom)
    if minimum < BOUNDARY_MIN_CROP_PIXELS:
        return image, {"left": 0, "top": 0, "right": 0, "bottom": 0}, False

    crop_width = width - left - right
    crop_height = height - top - bottom
    if crop_width < 32 or crop_height < 32:
        return image, {"left": 0, "top": 0, "right": 0, "bottom": 0}, False

    cropped = image.crop((left, top, width - right, height - bottom))
    return cropped, {"left": left, "top": top, "right": right, "bottom": bottom}, True


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

        is_border_touching = (
            min_x == 0 or min_y == 0
            or max_x == width - 1 or max_y == height - 1
        )

        boxes.append(
            {
                "x": padded_min_x,
                "y": padded_min_y,
                "width": padded_max_x - padded_min_x + 1,
                "height": padded_max_y - padded_min_y + 1,
                "_raw_x": min_x,
                "_raw_y": min_y,
                "_raw_width": max_x - min_x + 1,
                "_raw_height": max_y - min_y + 1,
                "_pixel_area": area,
                "_border_touching": is_border_touching,
            }
        )

    boxes.sort(key=lambda b: (b["y"], b["x"], b["width"], b["height"]))
    return boxes


def _to_scoring_windows(windows: list[dict]) -> list[dict]:
    """Return tight unpadded bounding boxes for windows that pass fill-ratio check.

    A window passes when the number of key-color pixels inside its tight bounding
    box is at least SCORE_MIN_FILL_RATIO of the box area.  This excludes sparse
    or stray matched regions so only solid, uniform window interiors contribute
    to scoring.
    """
    result = []
    for win in windows:
        if win.get("_border_touching"):
            continue
        raw_w = win.get("_raw_width")
        raw_h = win.get("_raw_height")
        pixel_area = win.get("_pixel_area", 0)
        if raw_w is None or raw_h is None:
            continue
        box_area = raw_w * raw_h
        if box_area <= 0:
            continue
        if pixel_area / box_area < SCORE_MIN_FILL_RATIO:
            continue
        result.append({
            "x": win["_raw_x"],
            "y": win["_raw_y"],
            "width": raw_w,
            "height": raw_h,
        })
    return result


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
    match_count = 0
    for idx, (r, g, b, _) in enumerate(pixels):
        if _is_key_color(r, g, b, key_color):
            mask[idx] = 1
            match_count += 1

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Color {key_color} matched {match_count} pixels (tolerance={KEY_COLOR_TOLERANCE})")

    cleanup_mask = bytearray(mask)
    _dilate_mask(cleanup_mask, width, height, radius=RENDER_MASK_DILATION_RADIUS)

    windows = _connected_components(mask, width, height)

    return mask, cleanup_mask, windows


async def outline_windows_from_image(
    image_url: str,
    key_color: str | tuple[int, int, int] | list[int] | None = None,
    allow_key_fallback: bool = True,
) -> dict[str, Any]:
    """Build deterministic window boxes and visualization layers from chroma-key windows."""
    image_bytes, _ = await _decode_image_url(image_url)
    source_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    boundary_color = _estimate_boundary_color(source_image)
    base, crop_box, crop_applied = _crop_boundary(source_image, boundary_color)
    cropped_background_url = _encode_png_data_uri(base)

    width, height = base.size
    pixels = list(base.getdata())
    resolved_key_color = _parse_key_color(key_color)

    candidate_colors: list[tuple[int, int, int]] = [resolved_key_color]
    if boundary_color is not None and boundary_color not in candidate_colors:
        candidate_colors.append(boundary_color)
    for candidate in KEY_COLOR_CANDIDATES:
        if candidate not in candidate_colors:
            candidate_colors.append(candidate)

    mask, cleanup_mask, windows = _build_masks_for_key(
        pixels,
        width,
        height,
        resolved_key_color,
    )

    scoring_windows = _to_scoring_windows(windows)
    best_score = _score_windows(scoring_windows, width, height)
    if allow_key_fallback and best_score <= 0.0:
        for candidate in candidate_colors:
            if candidate == resolved_key_color:
                continue

            candidate_mask, candidate_cleanup_mask, candidate_windows = _build_masks_for_key(
                pixels,
                width,
                height,
                candidate,
            )
            candidate_scoring = _to_scoring_windows(candidate_windows)
            candidate_score = _score_windows(candidate_scoring, width, height)
            if candidate_score > best_score:
                resolved_key_color = candidate
                mask = candidate_mask
                cleanup_mask = candidate_cleanup_mask
                windows = candidate_windows
                scoring_windows = candidate_scoring
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

    clean_windows = [
        {k: v for k, v in w.items() if not k.startswith("_")}
        for w in windows
        if not w.get("_border_touching")
    ]
    return {
        "windows": clean_windows,
        "scoring_windows": scoring_windows,
        "cropped_background_url": cropped_background_url,
        "processed_background_url": _encode_png_data_uri(processed),
        "overlay_url": _encode_png_data_uri(overlay),
        "mask_url": _encode_png_data_uri(mask_image),
        "board_width": width,
        "board_height": height,
        "window_key_color": _key_color_hex(resolved_key_color),
        "boundary_color": _key_color_hex(boundary_color) if boundary_color is not None else None,
        "boundary_crop_applied": crop_applied,
        "boundary_crop_box": crop_box,
        "candidate_key_colors": [_key_color_hex(color) for color in candidate_colors],
    }
