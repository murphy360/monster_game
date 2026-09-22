"""Regression test for strict vs. loose window detection.

Reproduces the real bug: a color-drifted patch that loosely resembles the key
color (within the broad per-pixel match tolerance) but isn't a real window
must never be returned as a window or masked out of the background - only
regions that also pass the strict fill-ratio/uniformity check should.
"""

import asyncio
import base64
import io

from PIL import Image

from backend.ai.window_outline import WINDOW_DARK_FILL, outline_windows_from_image

SCENE_COLOR = (40, 60, 90)
KEY_COLOR = (167, 239, 70)
# Within the broad per-pixel match tolerance (30/channel) so it's still
# detected as a candidate region, but outside the strict scoring tolerance
# (10/channel) so it must fail validation - analogous to sky haze or a
# shadow that happens to drift into the key color's broad match band.
DRIFTED_COLOR = (147, 219, 90)

REAL_WINDOW_BOX = (50, 50, 90, 80)  # x0, y0, x1, y1
STRAY_PATCH_BOX = (120, 20, 150, 45)


def _build_test_image_data_uri() -> str:
    image = Image.new("RGB", (200, 150), SCENE_COLOR)
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

    x0, y0, x1, y1 = STRAY_PATCH_BOX
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
