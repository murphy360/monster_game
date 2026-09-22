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
    (255, 0, 255),
    (255, 106, 19),
)
KEY_COLOR_TOLERANCE = 30
KEY_COLOR_DISTANCE_MAX = 140
MIN_COMPONENT_AREA = 500
MIN_COMPONENT_SIDE = 20
RENDER_MASK_DILATION_RADIUS = 2
# This only sizes the returned window rect (sprite placement/click area) -
# actual background masking now separately follows each window's real painted
# shape (see _mask_window_by_shape), since a fixed padding can't reliably
# predict how far a window's true visual edge extends past its tightly
# color-matched box, let alone what shape that edge is. Kept small so the
# AI-painted 2px black outline around each window still reads as a visible
# frame rather than getting fully absorbed into the returned box.
WINDOW_BOX_PADDING = 2
SCORE_MIN_FILL_RATIO = 0.75
SCORE_STRICT_COLOR_TOLERANCE = 10
SCORE_STRICT_UNIFORM_RATIO = 0.92
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
        return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))

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

    if key_color == (255, 0, 255):
        # Key fuchsia (#FF00FF) should have very strong red/blue and low green.
        return r >= 170 and b >= 170 and g <= 85 and abs(r - b) <= 55

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
    r, g, b = key_color
    return f"#{r:02X}{g:02X}{b:02X}"


def _is_color_match(
    rgb: tuple[int, int, int],
    target: tuple[int, int, int],
    tolerance: int = BOUNDARY_COLOR_TOLERANCE,
) -> bool:
    """Return True when a color is within per-channel tolerance of target."""
    return all(abs(channel - reference) <= tolerance for channel, reference in zip(rgb, target, strict=True))


def _count_strict_color_matches_in_box(
    pixels: list[tuple[int, int, int, int]],
    img_width: int,
    raw_x: int,
    raw_y: int,
    raw_w: int,
    raw_h: int,
    key_color: tuple[int, int, int],
    tolerance: int = SCORE_STRICT_COLOR_TOLERANCE,
) -> tuple[int, int]:
    """Return strict key-color match count and sampled area for a raw window box.

    We ignore a 1px ring (when possible) to avoid edge anti-alias artifacts and
    focus on the interior fill that should be uniformly chroma colored.
    """
    inset = 1 if raw_w >= 5 and raw_h >= 5 else 0
    sample_x = raw_x + inset
    sample_y = raw_y + inset
    sample_w = raw_w - (inset * 2)
    sample_h = raw_h - (inset * 2)
    if sample_w <= 0 or sample_h <= 0:
        sample_x = raw_x
        sample_y = raw_y
        sample_w = raw_w
        sample_h = raw_h

    matches = 0
    sample_area = sample_w * sample_h
    for dy in range(sample_h):
        for dx in range(sample_w):
            idx = (sample_y + dy) * img_width + (sample_x + dx)
            r, g, b, _ = pixels[idx]
            if _is_color_match((r, g, b), key_color, tolerance=tolerance):
                matches += 1
    return matches, sample_area


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
        key = (
            r // BOUNDARY_COLOR_BUCKET_SIZE,
            g // BOUNDARY_COLOR_BUCKET_SIZE,
            b // BOUNDARY_COLOR_BUCKET_SIZE,
        )
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


def _mask_window_by_shape(
    cleanup_mask: bytearray,
    loose_match_mask: bytearray,
    width: int,
    height: int,
    win: dict[str, int],
    margin: int = 6,
) -> None:
    """Mask a window's actual painted silhouette, not a rectangle around it.

    Gemini doesn't always paint rectangular window openings - round, arched,
    and other shapes are common, especially for whimsical themes. Forcing a
    rectangular mask over a round window either cuts into its frame's rounded
    corners or leaves them showing the raw, unprocessed color. Instead, within
    a small bounded region around this window, flood-fill from the region's
    border across unmatched pixels; anything *not* reached is fully enclosed
    by matched pixels (a hole from shading/gradient in the AI-rendered fill,
    not part of the window's true outside) and gets masked too. The result
    hugs whatever shape the window actually is, while still closing small
    internal gaps - the reason a blind rectangle fill existed before.

    Bounded to a small margin around this specific window so it can't balloon
    into unrelated background the way masking the whole image's loose match
    did previously.
    """
    x = int(win.get("x", 0))
    y = int(win.get("y", 0))
    w = int(win.get("width", 0))
    h = int(win.get("height", 0))
    if w <= 0 or h <= 0:
        return

    min_x = max(0, x - margin)
    min_y = max(0, y - margin)
    max_x = min(width - 1, x + w - 1 + margin)
    max_y = min(height - 1, y + h - 1 + margin)
    region_w = max_x - min_x + 1
    region_h = max_y - min_y + 1

    reached_from_outside = bytearray(region_w * region_h)
    queue: deque[tuple[int, int]] = deque()

    def _enqueue(rx: int, ry: int) -> None:
        if 0 <= rx < region_w and 0 <= ry < region_h:
            ridx = ry * region_w + rx
            gidx = (min_y + ry) * width + (min_x + rx)
            if not reached_from_outside[ridx] and not loose_match_mask[gidx]:
                reached_from_outside[ridx] = 1
                queue.append((rx, ry))

    for rx in range(region_w):
        _enqueue(rx, 0)
        _enqueue(rx, region_h - 1)
    for ry in range(region_h):
        _enqueue(0, ry)
        _enqueue(region_w - 1, ry)

    while queue:
        rx, ry = queue.popleft()
        _enqueue(rx + 1, ry)
        _enqueue(rx - 1, ry)
        _enqueue(rx, ry + 1)
        _enqueue(rx, ry - 1)

    for ry in range(region_h):
        for rx in range(region_w):
            gidx = (min_y + ry) * width + (min_x + rx)
            if loose_match_mask[gidx] or not reached_from_outside[ry * region_w + rx]:
                cleanup_mask[gidx] = 1


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

        is_border_touching = min_x == 0 or min_y == 0 or max_x == width - 1 or max_y == height - 1

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


def _to_scoring_windows(
    windows: list[dict],
    mask: bytearray | None = None,
    img_width: int | None = None,
    pixels: list[tuple[int, int, int, int]] | None = None,
    key_color: tuple[int, int, int] | None = None,
    require_strict_uniformity: bool = True,
) -> list[dict]:
    """Return tight unpadded bounding boxes for windows that pass fill-ratio check.

    A window passes when the number of key-color pixels inside its tight bounding
    box is at least SCORE_MIN_FILL_RATIO of the box area. This is fundamentally a
    *shape* check: a roughly rectangular, solidly-filled region (a real window,
    even one with some internal shading/gradient) passes it, while an irregular
    stray patch (sky haze, a shadow) - whose matched pixels only sparsely or
    unevenly fill their own bounding box - does not. That makes it a reliable
    signal for "is this actually a window" independent of exactly how uniform its
    color is.

    When *mask* and *img_width* are provided the fill ratio is computed by counting
    every mask pixel that falls within the raw bounding box, which is more accurate
    than relying solely on the connected-component pixel count.

    When *pixels*, *img_width*, and *key_color* are provided and
    *require_strict_uniformity* is True (the default), an additional strict
    uniformity check rejects boxes whose interior is not mostly the *exact* key
    color - this is the higher bar for treating a window as good enough to be an
    interactive gameplay element, not for deciding whether to mask it at all.
    Pass False to skip this and keep only the shape check, e.g. for deciding what
    to mask out of the background: a window that's clearly real but imperfectly
    colored should still never show its raw chroma-key fill in the final image.
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
        if mask is not None and img_width is not None:
            raw_x = win.get("_raw_x", 0)
            raw_y = win.get("_raw_y", 0)
            mask_count = sum(
                1
                for dy in range(raw_h)
                for dx in range(raw_w)
                if mask[(raw_y + dy) * img_width + (raw_x + dx)]
            )
            fill_ratio = mask_count / box_area
        else:
            fill_ratio = pixel_area / box_area
        if fill_ratio < SCORE_MIN_FILL_RATIO:
            continue
        if (
            require_strict_uniformity
            and pixels is not None
            and img_width is not None
            and key_color is not None
        ):
            strict_count, strict_area = _count_strict_color_matches_in_box(
                pixels,
                img_width,
                win["_raw_x"],
                win["_raw_y"],
                raw_w,
                raw_h,
                key_color,
            )
            if strict_area <= 0:
                continue
            strict_ratio = strict_count / strict_area
            if strict_ratio < SCORE_STRICT_UNIFORM_RATIO:
                continue
        result.append(
            {
                "x": win["_raw_x"],
                "y": win["_raw_y"],
                "width": raw_w,
                "height": raw_h,
            }
        )
    return result


def _accepted_padded_windows(
    windows: list[dict[str, int]],
    scoring_windows: list[dict[str, int]],
) -> list[dict[str, int]]:
    """Return the padded window boxes whose raw region passed strict scoring.

    ``windows`` (padded, one per connected component) and ``scoring_windows``
    (tight/raw, filtered by fill-ratio and color uniformity) are both derived
    from the same connected components, so a raw-coordinate match reliably
    identifies which padded boxes are actually validated windows rather than
    stray look-alike patches that only cleared the loose area/side filter.
    """
    accepted_raw = {(win["x"], win["y"], win["width"], win["height"]) for win in scoring_windows}
    return [
        win
        for win in windows
        if not win.get("_border_touching")
        and (win.get("_raw_x"), win.get("_raw_y"), win.get("_raw_width"), win.get("_raw_height"))
        in accepted_raw
    ]


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
) -> tuple[bytearray, list[dict[str, int]]]:
    """Build the raw per-pixel match mask plus detected windows for one key color.

    This mask is used only to score candidate colors and compute fill ratios;
    the mask that actually gets blacked out of the background is built later,
    from validated windows only, so stray look-alike scene pixels never get
    treated as part of a window (see ``outline_windows_from_image``).
    """
    mask = bytearray(width * height)
    match_count = 0
    for idx, (r, g, b, _) in enumerate(pixels):
        if _is_key_color(r, g, b, key_color):
            mask[idx] = 1
            match_count += 1

    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Color {key_color} matched {match_count} pixels (tolerance={KEY_COLOR_TOLERANCE})")

    windows = _connected_components(mask, width, height)

    return mask, windows


async def outline_windows_from_image(
    image_url: str,
    key_color: str | tuple[int, int, int] | list[int] | None = None,
    allow_key_fallback: bool = True,
    force_key_color: bool = False,
) -> dict[str, Any]:
    """Build deterministic window boxes and visualization layers from chroma-key windows.

    By default, when the image has a detectable uniform border band, that
    band's color is trusted over *key_color* - it's more authoritative than a
    caller-supplied guess, since it's read directly from what the image
    actually contains. Pass *force_key_color=True* to disable that and detect
    using exactly *key_color*: this matters when a caller is deliberately
    comparing several specific candidate colors against each other (see
    GeminiAdapter._select_best_key_color), where silently substituting the
    same boundary color for every candidate would make them indistinguishable.
    """
    image_bytes, _ = await _decode_image_url(image_url)
    source_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    boundary_color = _estimate_boundary_color(source_image)
    base, crop_box, crop_applied = _crop_boundary(source_image, boundary_color)
    cropped_background_url = _encode_png_data_uri(base)

    width, height = base.size
    pixels = list(base.getdata())
    requested_key_color = _parse_key_color(key_color)
    if force_key_color:
        resolved_key_color = requested_key_color
    else:
        resolved_key_color = boundary_color if boundary_color is not None else requested_key_color

    candidate_colors: list[tuple[int, int, int]] = [resolved_key_color]
    if requested_key_color not in candidate_colors:
        candidate_colors.append(requested_key_color)
    for candidate in KEY_COLOR_CANDIDATES:
        if candidate not in candidate_colors:
            candidate_colors.append(candidate)

    mask, windows = _build_masks_for_key(
        pixels,
        width,
        height,
        resolved_key_color,
    )

    scoring_windows = _to_scoring_windows(
        windows,
        mask,
        width,
        pixels,
        resolved_key_color,
    )
    best_score = _score_windows(scoring_windows, width, height)
    if allow_key_fallback and best_score <= 0.0:
        for candidate in candidate_colors:
            if candidate == resolved_key_color:
                continue

            candidate_mask, candidate_windows = _build_masks_for_key(
                pixels,
                width,
                height,
                candidate,
            )
            candidate_scoring = _to_scoring_windows(
                candidate_windows,
                candidate_mask,
                width,
                pixels,
                candidate,
            )
            candidate_score = _score_windows(candidate_scoring, width, height)
            if candidate_score > best_score:
                resolved_key_color = candidate
                mask = candidate_mask
                windows = candidate_windows
                scoring_windows = candidate_scoring
                best_score = candidate_score

    # Two different bars matter from here on, using two different signals:
    # - accepted_windows (passed fill-ratio AND strict color-uniformity, in
    #   scoring_windows): well-formed AND precisely colored enough to trust
    #   as an interactive gameplay window.
    # - maskable_windows (passed fill-ratio only - a shape check that a real,
    #   roughly-rectangular window passes even with imperfect internal
    #   shading/gradient, but an irregular stray patch like sky haze or a
    #   shadow does not): everything that's clearly a real window, even if
    #   not precisely-colored enough to be interactive. Every one of these
    #   still needs to be masked out, or a window that narrowly missed
    #   strict validation is left showing its raw, unprocessed chroma-key
    #   fill in the final background instead of blending in.
    accepted_windows = _accepted_padded_windows(windows, scoring_windows)
    shape_passed_windows = _to_scoring_windows(windows, mask, width, require_strict_uniformity=False)
    maskable_windows = _accepted_padded_windows(windows, shape_passed_windows)

    cleanup_mask = bytearray(width * height)
    for maskable_win in maskable_windows:
        _mask_window_by_shape(cleanup_mask, mask, width, height, maskable_win)
    _dilate_mask(cleanup_mask, width, height, radius=RENDER_MASK_DILATION_RADIUS)

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

    clean_windows = [{k: v for k, v in w.items() if not k.startswith("_")} for w in accepted_windows]
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
