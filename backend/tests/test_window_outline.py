"""Regression test for shape-based vs. loose window detection.

Reproduces the real bug: an irregular stray patch that loosely resembles the
key color (within the broad per-pixel match tolerance) but isn't a real
window - e.g. sky haze or a shadow, which are irregular/organic rather than
rectangular - must never be returned as a window or masked out of the
background. A real window is still fundamentally rectangular even when
imperfectly colored, which is what the fill-ratio (shape) check distinguishes;
color uniformity is a separate, stricter bar reserved for deciding what's
interactive (see test_candidate_scoring... below and window_outline.py's
docstrings for _to_scoring_windows).
"""

import asyncio
import base64
import io

from PIL import Image

from backend.ai.window_outline import WINDOW_DARK_FILL, outline_windows_from_image

SCENE_COLOR = (40, 60, 90)
KEY_COLOR = (167, 239, 70)
# Within the broad per-pixel match tolerance (30/channel), same family as
# KEY_COLOR - the point of this patch is that it's irregularly shaped, not
# that its color is off.
DRIFTED_COLOR = (147, 219, 90)

REAL_WINDOW_BOX = (50, 50, 90, 80)  # x0, y0, x1, y1
# A diagonal stripe within this bounding box, not a filled rectangle: its
# matched pixels only sparsely fill their own bounding box (low fill-ratio),
# same as real irregular scene content like sky haze or a shadow would.
STRAY_PATCH_BOX = (110, 15, 190, 95)
STRAY_PATCH_BAND_HALF_WIDTH = 4
# A solid rectangle (passes the shape/fill-ratio check, same as a real
# window) filled with DRIFTED_COLOR instead of the exact key color - fails
# only the strict color-uniformity check. Represents a real window Gemini
# rendered with some gradient/shading rather than a perfectly flat fill.
IMPRECISE_WINDOW_BOX = (50, 120, 90, 150)


def _build_test_image_data_uri() -> str:
    image = Image.new("RGB", (220, 170), SCENE_COLOR)
    pixels = image.load()
    width, height = image.size

    # Give each edge a distinct flat color (thicker than the 12px boundary
    # sample band) so the image has no single uniform "border band" color -
    # otherwise the real boundary-band detection (meant for the intentional
    # border Gemini paints) picks up this synthetic image's plain background
    # as if it were that border, and hijacks the resolved key color.
    band = 15
    for y in range(height):
        for x in range(width):
            if y < band:
                pixels[x, y] = (10, 10, 200)
            elif y >= height - band:
                pixels[x, y] = (10, 200, 10)
            elif x < band:
                pixels[x, y] = (200, 200, 10)
            elif x >= width - band:
                pixels[x, y] = (200, 10, 10)

    x0, y0, x1, y1 = REAL_WINDOW_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = KEY_COLOR

    sx0, sy0, sx1, sy1 = STRAY_PATCH_BOX
    for y in range(sy0, sy1):
        for x in range(sx0, sx1):
            if abs((x - sx0) - (y - sy0)) <= STRAY_PATCH_BAND_HALF_WIDTH:
                pixels[x, y] = DRIFTED_COLOR

    x0, y0, x1, y1 = IMPRECISE_WINDOW_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = DRIFTED_COLOR

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/png;base64," + b64


def _decode_pixel(data_uri: str, x: int, y: int) -> tuple[int, int, int, int]:
    header, encoded = data_uri.split(",", 1)
    image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
    return image.getpixel((x, y))


def test_stray_color_drifted_patch_is_not_treated_as_a_window() -> None:
    data_uri = _build_test_image_data_uri()

    result = asyncio.run(outline_windows_from_image(data_uri, key_color="#A7EF46", allow_key_fallback=False))

    windows = result["windows"]
    assert len(windows) == 1

    real_x0, real_y0, real_x1, real_y1 = REAL_WINDOW_BOX
    win = windows[0]
    assert win["x"] <= real_x0
    assert win["y"] <= real_y0
    assert win["x"] + win["width"] >= real_x1
    assert win["y"] + win["height"] >= real_y1

    stray_x0, stray_y0, stray_x1, stray_y1 = STRAY_PATCH_BOX
    stray_center = ((stray_x0 + stray_x1) // 2, (stray_y0 + stray_y1) // 2)
    for win in windows:
        assert not (
            win["x"] <= stray_center[0] <= win["x"] + win["width"]
            and win["y"] <= stray_center[1] <= win["y"] + win["height"]
        )

    processed_url = result["processed_background_url"]
    real_center = ((real_x0 + real_x1) // 2, (real_y0 + real_y1) // 2)
    assert _decode_pixel(processed_url, *real_center) == WINDOW_DARK_FILL
    assert _decode_pixel(processed_url, *stray_center)[:3] == DRIFTED_COLOR


def test_window_that_fails_strict_color_check_is_masked_but_not_returned() -> None:
    """Regression test: a real, rectangular window that Gemini rendered with
    some gradient/shading (failing only the strict color-uniformity check,
    not the shape check) must still be masked out of the background - it
    just shouldn't be returned as an interactive gameplay window. Previously
    the code used the same strict-only window set for both, so a window like
    this was left showing its raw, unprocessed chroma-key fill instead of
    blending into the background."""
    data_uri = _build_test_image_data_uri()

    result = asyncio.run(outline_windows_from_image(data_uri, key_color="#A7EF46", allow_key_fallback=False))

    ix0, iy0, ix1, iy1 = IMPRECISE_WINDOW_BOX
    imprecise_center = ((ix0 + ix1) // 2, (iy0 + iy1) // 2)

    for win in result["windows"]:
        assert not (
            win["x"] <= imprecise_center[0] <= win["x"] + win["width"]
            and win["y"] <= imprecise_center[1] <= win["y"] + win["height"]
        )

    assert _decode_pixel(result["processed_background_url"], *imprecise_center) == WINDOW_DARK_FILL


BORDER_COLOR = (167, 239, 70)  # lime - matches the real pipeline's default key color
FUCHSIA_WINDOW_BOX = (60, 50, 100, 80)


def _build_bordered_image_data_uri() -> str:
    """An image with one uniform border band (triggers boundary detection) and
    a single fuchsia window - no lime content anywhere inside the border."""
    image = Image.new("RGB", (200, 150), SCENE_COLOR)
    pixels = image.load()
    width, height = image.size

    band = 15
    for y in range(height):
        for x in range(width):
            if y < band or y >= height - band or x < band or x >= width - band:
                pixels[x, y] = BORDER_COLOR

    x0, y0, x1, y1 = FUCHSIA_WINDOW_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = (255, 0, 255)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/png;base64," + b64


def test_candidate_scoring_must_use_the_requested_color_not_the_boundary_color() -> None:
    """Regression test: outline_windows_from_image() silently replaced whatever
    key_color was requested with the image's auto-detected border-band color,
    so every candidate in GeminiAdapter._select_best_key_color's comparison
    loop collapsed to testing the same (border) color - candidates never
    actually differed, no matter which color was requested."""
    data_uri = _build_bordered_image_data_uri()

    forced = asyncio.run(
        outline_windows_from_image(
            data_uri, key_color="#FF00FF", allow_key_fallback=False, force_key_color=True
        )
    )
    assert len(forced["windows"]) == 1

    not_forced = asyncio.run(
        outline_windows_from_image(data_uri, key_color="#FF00FF", allow_key_fallback=False)
    )
    assert not_forced["window_key_color"] == "#A7EF46"
    assert len(not_forced["windows"]) == 0


# A window with its four corners chamfered off (an arched/rounded-window
# stand-in) rather than a plain rectangle. The chamfer removes only ~4.5% of
# the bounding box area, comfortably above both SCORE_MIN_FILL_RATIO and
# SCORE_STRICT_UNIFORM_RATIO, so this window is a real, accepted, interactive
# one - but a rectangle mask over it would still cover the four chamfered
# corners, which is exactly the bug this test guards against: the mask should
# hug the actual painted silhouette, not its bounding box.
ARCHED_WINDOW_BOX = (50, 50, 150, 150)  # x0, y0, x1, y1 - 100x100
ARCHED_WINDOW_CHAMFER = 15

# A tall, narrow window - e.g. a slender arch in a damaged/eroded section of
# architecture that's naturally thinner than the rest of a facade. 15px wide
# is a real, clearly intentional shape (high fill-ratio, decent total area),
# not a stray anti-aliasing artifact, but was previously excluded outright by
# MIN_COMPONENT_SIDE before ever reaching the shape/color checks.
NARROW_WINDOW_BOX = (100, 30, 115, 115)  # x0, y0, x1, y1 - 15x85

# A window shaped like a real arch: a semicircular top fused to a short
# rectangular body, rather than a plain rectangle or a token corner chamfer.
# Its bounding box naturally loses ~15% of its area to the box's own corners
# (outside the painted silhouette entirely, not anti-aliasing) - representative
# of real Gemini-painted arched windows.
ARCH_WINDOW_BOX = (60, 40, 140, 100)  # x0, y0, x1, y1 - 80x60


def _build_arched_window_image_data_uri() -> str:
    image = Image.new("RGB", (220, 170), SCENE_COLOR)
    pixels = image.load()
    width, height = image.size

    band = 15
    for y in range(height):
        for x in range(width):
            if y < band:
                pixels[x, y] = (10, 10, 200)
            elif y >= height - band:
                pixels[x, y] = (10, 200, 10)
            elif x < band:
                pixels[x, y] = (200, 200, 10)
            elif x >= width - band:
                pixels[x, y] = (200, 10, 10)

    x0, y0, x1, y1 = ARCHED_WINDOW_BOX
    c = ARCHED_WINDOW_CHAMFER
    for y in range(y0, y1):
        for x in range(x0, x1):
            dx = x - x0 if x - x0 < (x1 - x0) / 2 else (x1 - 1) - x
            dy = y - y0 if y - y0 < (y1 - y0) / 2 else (y1 - 1) - y
            if dx + dy < c:
                continue  # leave this corner pixel as SCENE_COLOR
            pixels[x, y] = KEY_COLOR

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/png;base64," + b64


def _build_narrow_window_image_data_uri() -> str:
    image = Image.new("RGB", (220, 170), SCENE_COLOR)
    pixels = image.load()
    width, height = image.size

    band = 15
    for y in range(height):
        for x in range(width):
            if y < band:
                pixels[x, y] = (10, 10, 200)
            elif y >= height - band:
                pixels[x, y] = (10, 200, 10)
            elif x < band:
                pixels[x, y] = (200, 200, 10)
            elif x >= width - band:
                pixels[x, y] = (200, 10, 10)

    x0, y0, x1, y1 = NARROW_WINDOW_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = KEY_COLOR

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/png;base64," + b64


def _build_semicircular_arch_window_image_data_uri() -> str:
    image = Image.new("RGB", (220, 170), SCENE_COLOR)
    pixels = image.load()
    width, height = image.size

    # Distinct color per edge, same as _build_test_image_data_uri, so the
    # image has no single uniform border band for boundary detection to pick
    # up and hijack the resolved key color with.
    band = 15
    for y in range(height):
        for x in range(width):
            if y < band:
                pixels[x, y] = (10, 10, 200)
            elif y >= height - band:
                pixels[x, y] = (10, 200, 10)
            elif x < band:
                pixels[x, y] = (200, 200, 10)
            elif x >= width - band:
                pixels[x, y] = (200, 10, 10)

    x0, y0, x1, y1 = ARCH_WINDOW_BOX
    box_w = x1 - x0
    radius = box_w // 2
    cx = x0 + radius
    arch_top = y0 + radius
    for y in range(y0, y1):
        for x in range(x0, x1):
            if y < arch_top:
                if (x - cx) ** 2 + (y - arch_top) ** 2 <= radius**2:
                    pixels[x, y] = KEY_COLOR
            else:
                pixels[x, y] = KEY_COLOR

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/png;base64," + b64


def test_mask_follows_a_non_rectangular_window_shape_instead_of_its_bounding_box() -> None:
    """Regression test: a window with chamfered (non-rectangular) corners -
    standing in for the round/arched windows Gemini commonly paints for
    whimsical themes - must have its actual silhouette masked, not a
    rectangle drawn around its bounding box. A rectangle mask would cover the
    chamfered corners too, showing as a black square jammed into a round
    window frame instead of a window that fits its frame."""
    data_uri = _build_arched_window_image_data_uri()

    result = asyncio.run(outline_windows_from_image(data_uri, key_color="#A7EF46", allow_key_fallback=False))
    assert len(result["windows"]) == 1

    x0, y0, x1, y1 = ARCHED_WINDOW_BOX
    processed_url = result["processed_background_url"]

    center = ((x0 + x1) // 2, (y0 + y1) // 2)
    assert _decode_pixel(processed_url, *center) == WINDOW_DARK_FILL

    corner_inset = 2  # a couple pixels in from the very corner, safely inside the chamfer
    corners = [
        (x0 + corner_inset, y0 + corner_inset),
        (x1 - 1 - corner_inset, y0 + corner_inset),
        (x0 + corner_inset, y1 - 1 - corner_inset),
        (x1 - 1 - corner_inset, y1 - 1 - corner_inset),
    ]
    for corner in corners:
        assert _decode_pixel(processed_url, *corner)[:3] == SCENE_COLOR


def test_narrow_window_is_still_detected() -> None:
    """Regression test: a real but narrow (15px-wide) window must still be
    detected and masked, not excluded outright by MIN_COMPONENT_SIDE before
    it ever reaches the shape/color checks."""
    data_uri = _build_narrow_window_image_data_uri()

    result = asyncio.run(outline_windows_from_image(data_uri, key_color="#A7EF46", allow_key_fallback=False))
    assert len(result["windows"]) == 1

    x0, y0, x1, y1 = NARROW_WINDOW_BOX
    center = ((x0 + x1) // 2, (y0 + y1) // 2)
    assert _decode_pixel(result["processed_background_url"], *center) == WINDOW_DARK_FILL


def test_real_arched_window_with_natural_corner_loss_is_still_interactive() -> None:
    """Regression test: a genuinely, exactly key-colored arched window loses
    ~15% of its bounding-box area to its own curved silhouette (the box's
    corners sit outside the painted window entirely) - not to imprecise
    coloring. That drags fill-ratio and strict color-uniformity down together,
    since they measure the same box against the same pixels. Previously
    SCORE_STRICT_UNIFORM_RATIO (0.92) sat above what a real arch can
    structurally achieve, so an exactly-colored arch that clearly passed the
    shape check as a genuine window was still excluded from the interactive
    window list."""
    data_uri = _build_semicircular_arch_window_image_data_uri()

    result = asyncio.run(outline_windows_from_image(data_uri, key_color="#A7EF46", allow_key_fallback=False))

    assert len(result["windows"]) == 1
    x0, y0, x1, y1 = ARCH_WINDOW_BOX
    win = result["windows"][0]
    assert win["x"] <= x0
    assert win["y"] <= y0
    assert win["x"] + win["width"] >= x1
    assert win["y"] + win["height"] >= y1
