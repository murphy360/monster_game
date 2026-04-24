# Window Outlining Process

This document explains how window detection works in this project today.

## Short Answer

We are using Gemini vision prompts (LLM-based image understanding), not classical computer vision.

There is no OpenCV contour detection, no edge detector, and no segmentation model running locally.

## Tools In Use

### 1) Background image generation

- File: `backend/ai/gemini_adapter.py`
- Method: `generate_background(theme)`
- Model: `gemini-2.5-flash-image` by default (or `GEMINI_IMAGE_MODEL`)
- Behavior:
  - Generates a 16:9 themed scene.
  - Tries to enforce empty windows/openings via prompt wording.
  - Normalizes the image to exactly 1280x720 pixels.

### 2) Window detection (outline extraction)

- File: `backend/ai/gemini_adapter.py`
- Method: `extract_bounding_boxes(image_url)`
- Model: `gemini-2.5-flash` by default (or `GEMINI_TEXT_MODEL`)
- Behavior:
  - Sends the image plus a strict prompt asking for only empty rectangular openings.
  - First pass returns candidate boxes.
  - Second pass refines/snaps those candidates to tighter window interiors.
  - Output format is JSON array:
    - `[{"x": int, "y": int, "width": int, "height": int}, ...]`

### 3) Post-processing and normalization

- File: `backend/routes/generate_level.py`
- Function: `_normalize_windows(...)`
- Behavior:
  - Coerces values to integers.
  - Clamps each box inside board bounds.
  - Sorts boxes deterministically.
  - Assigns stable sequential ids.

## Endpoints That Use It

### A) Game generation endpoint

- File: `backend/routes/generate_level.py`
- Route: `POST /generate-level`
- Flow:
  1. Generate background image.
  2. Extract window boxes from that exact generated image.
  3. Normalize boxes.
  4. Generate one sprite per detected window.
  5. Return `background_url`, `windows`, `sprite_urls`, `board_width`, `board_height`.

### B) Test outlining endpoint

- File: `backend/routes/serve_assets.py`
- Route: `POST /serve-assets`
- Flow:
  1. Accept an arbitrary uploaded image URL/data URI.
  2. Run `extract_bounding_boxes(...)` on that image.
  3. Return the detected window boxes.

## Frontend Rendering and Alignment

- File: `frontend/src/components/GameBoard.jsx`
- Behavior:
  - Renders background as an actual image layer (`object-fit: contain`), not a cropped CSS cover background.
  - Computes image-space to viewport transform.
  - Applies window boxes in the same board coordinate system.

This is what keeps overlay boxes aligned with the displayed image geometry.

## Test Page Workflow

- File: `frontend/src/components/TestPage.jsx`
- Route/UI page: `/test`
- Behavior:
  1. Upload image.
  2. Display it as board background.
  3. Click Outline Windows.
  4. Backend runs `/serve-assets` and returns boxes.
  5. Boxes are rendered in calibration mode (no gameplay pop-ins).

## Important Limits

Because this is prompt-based visual extraction (LLM vision), results can vary by image style and ambiguity.

Common failure cases:
- Very small images.
- Heavy perspective distortion.
- Occluded windows.
- Non-rectangular openings.
- Illustrations where windows are stylized inconsistently.

## Why This Can Look Like "Snapping"

The current "snap" behavior is semantic, not geometric optimization.

Meaning:
- We ask the model twice (detect, then refine).
- The model adjusts coordinates based on visual understanding.
- We do not run pixel-level geometric fitting after that.

If needed later, we can add a deterministic geometric refinement layer after AI output.

## Model Configuration Notes

- The adapter uses `GEMINI_TEXT_MODEL` and `GEMINI_IMAGE_MODEL` directly from environment/config.
- Background generation and sprite generation use the image model.
- Window extraction and config JSON generation use the text/vision model.
