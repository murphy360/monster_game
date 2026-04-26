"""Concrete AIGenerator implementation backed by Google Gemini.

Uses:
- ``gemini-2.5-flash`` (or ``GEMINI_TEXT_MODEL``) for text generation and vision.
- ``gemini-2.5-flash-image`` (or ``GEMINI_IMAGE_MODEL``) for image generation.

Two image-generation backends are supported automatically:
- Imagen models (model name starts with ``imagen``): uses ``generate_images``.
- Nano Banana / Gemini image models: uses ``generate_content`` with
  ``response_modalities=["IMAGE"]``.

The adapter reads ``GEMINI_API_KEY`` from the environment (or a .env file
loaded by python-dotenv). Model IDs can be overridden with
``GEMINI_TEXT_MODEL`` and ``GEMINI_IMAGE_MODEL``.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from typing import Any

from PIL import Image

import httpx
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

from .base import AIGenerator, BackgroundAttemptCallback, GeneratedBackground
from .window_outline import outline_windows_from_image

load_dotenv()

logger = logging.getLogger(__name__)


class GeminiAdapter(AIGenerator):
    """AIGenerator implementation using Google Gemini models."""

    # Text / vision model
    DEFAULT_TEXT_MODEL = "gemini-2.5-flash"
    # Image generation model (Nano Banana by default)
    DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
    BACKGROUND_WIDTH = 1280
    BACKGROUND_HEIGHT = 720
    BACKGROUND_MAX_RETRIES = 2
    WINDOW_KEY_COLORS = (
        "#A7EF46",
        "#9A2E82",
        "#FF6A13",
    )
    WINDOW_KEY_COLOR_LABELS = {
        "#A7EF46": "neon lime",
        "#9A2E82": "deep magenta",
        "#FF6A13": "vivid orange",
    }
    KEY_COLOR_TOP_WINDOW_COUNT = 5
    KEY_COLOR_MODEL_MATCH_BONUS = 15000.0
    KEY_COLOR_ABSURD_BOX_AREA_RATIO = 0.22

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY environment variable is not set. "
                "Create a .env file in the backend/ directory with GEMINI_API_KEY=<your key>."
            )
        self._client = genai.Client(api_key=api_key)
        self._text_model = os.getenv("GEMINI_TEXT_MODEL", self.DEFAULT_TEXT_MODEL)
        self._image_model = os.getenv("GEMINI_IMAGE_MODEL", self.DEFAULT_IMAGE_MODEL)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate_level_config(self, theme: str) -> dict[str, Any]:
        """Ask Gemini to produce a JSON level configuration."""
        prompt = (
            f"You are a game designer for a whack-a-mole style monster game.\n"
            f"Theme: {theme}\n\n"
            "Return ONLY a JSON object (no markdown fences) with this exact shape:\n"
            "{\n"
            '  "title": "<level name>",\n'
            '  "windows": [\n'
            '    {"id": 1, "x": <int>, "y": <int>, "width": <int>, "height": <int>},\n'
            "    ... (6-8 windows, coordinates fit a 1280x720 canvas)\n"
            "  ],\n"
            '  "monster_names": ["<short spooky name for monster 1>", ...],\n'
            '  "monster_flavor": ["<one short punchy tagline for monster 1>", ...],\n'
            '  "monster_descriptions": ["<detailed image-generation description for monster 1>", ...]\n'
            "}\n\n"
            "monster_names: short, evocative names (2-4 words, e.g. 'The Wailing Widow').\n"
            "monster_flavor: one witty or spooky sentence shown to the player while loading.\n"
            "monster_descriptions: detailed visual descriptions used to generate sprite art."
        )
        response = await self._client.aio.models.generate_content(
            model=self._text_model,
            contents=prompt,
        )
        raw = response.text.strip()
        # Strip optional markdown fences
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)

    @staticmethod
    def _strip_white_background(image_bytes: bytes, threshold: int = 240) -> bytes:
        """Replace near-white pixels with transparency and return PNG bytes."""
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        r, g, b, a = img.split()
        r_data = r.getdata()
        g_data = g.getdata()
        b_data = b.getdata()
        new_a = [
            0 if (rv >= threshold and gv >= threshold and bv >= threshold) else av
            for rv, gv, bv, av in zip(r_data, g_data, b_data, a.getdata())
        ]
        a.putdata(new_a)
        img = Image.merge("RGBA", (r, g, b, a))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _resize_background_to_canvas(
        image_bytes: bytes,
        target_width: int,
        target_height: int,
    ) -> bytes:
        """Resize generated background to an exact target pixel canvas."""
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        resized = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _strip_markdown_fences(raw: str) -> str:
        """Remove optional markdown code fences from model output."""
        raw = raw.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return raw.strip()

    @classmethod
    def _safe_load_json(cls, raw: str, default: Any) -> Any:
        """Best-effort JSON parse with a safe fallback value."""
        try:
            return json.loads(cls._strip_markdown_fences(raw))
        except json.JSONDecodeError:
            logger.warning("Failed to parse model JSON; using fallback value")
            return default

    async def _has_occupied_windows(self, image_bytes: bytes, mime_type: str) -> bool:
        """Return True when the generated background already contains characters in windows."""
        image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        prompt = (
            "Check whether this scene has any people, monsters, animals, or characters inside "
            "the INTERIOR area of architectural windows/openings where gameplay sprites should appear. "
            "Ignore characters that are outside openings (for example on rooftops, balconies, decks, or ground). "
            "Respond with ONLY JSON object: {\"has_occupied_windows\": true|false}."
        )
        response = await self._client.aio.models.generate_content(
            model=self._text_model,
            contents=[image_part, prompt],
        )
        parsed = self._safe_load_json(response.text or "", {"has_occupied_windows": False})
        return bool(parsed.get("has_occupied_windows", False))

    async def _generate_image_bytes(self, prompt: str, aspect_ratio: str = "1:1") -> tuple[bytes, str]:
        """Generate an image and return (raw_bytes, mime_type), routing to the correct API."""
        if self._image_model.lower().startswith("imagen"):
            result = await self._client.aio.models.generate_images(
                model=self._image_model,
                prompt=prompt,
                config=genai_types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                ),
            )
            return result.generated_images[0].image.image_bytes, "image/png"
        else:
            # Nano Banana / Gemini image models use generate_content
            response = await self._client.aio.models.generate_content(
                model=self._image_model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None and not getattr(part, "thought", False):
                    return part.inline_data.data, part.inline_data.mime_type or "image/png"
            raise ValueError("No image part found in Gemini response")

    async def _generate_image(self, prompt: str, aspect_ratio: str = "1:1") -> str:
        """Generate an image and return a data-URI."""
        image_bytes, mime = await self._generate_image_bytes(prompt, aspect_ratio)
        b64 = base64.b64encode(image_bytes).decode()
        return f"data:{mime};base64,{b64}"

    @classmethod
    def _window_key_color_list_text(cls) -> str:
        """Return supported key colors with text aliases for prompting."""
        return ", ".join(
            f"{color} ({cls.WINDOW_KEY_COLOR_LABELS.get(color, 'key color')})"
            for color in cls.WINDOW_KEY_COLORS
        )

    @classmethod
    def _window_key_color_label(cls, color: str) -> str:
        """Return a human-friendly text alias for a supported key color."""
        return cls.WINDOW_KEY_COLOR_LABELS.get(color, "key color")

    @classmethod
    def _normalize_key_color_choice(cls, value: str) -> str | None:
        """Resolve a model-returned key color that may be hex or text label."""
        normalized = value.strip().lower()
        if not normalized:
            return None

        for color in cls.WINDOW_KEY_COLORS:
            if normalized == color.lower():
                return color

        for color, label in cls.WINDOW_KEY_COLOR_LABELS.items():
            if normalized == label.lower():
                return color

        return None

    async def _choose_key_color_for_theme(self, theme: str) -> tuple[str, str]:
        """Ask the text model to choose the best mask color from supported options."""
        options_text = self._window_key_color_list_text()
        prompt = (
            "You are choosing a chroma-key mask color for a game background. "
            f"Theme: {theme}. "
            f"Allowed colors: {options_text}. "
            "Choose the single color that is MOST visually distinct from the likely scene palette for this theme. "
            "Prioritize avoiding likely dominant scene hues. "
            "Respond with ONLY JSON: {\"window_key_color\": \"#RRGGBB or color label\"}."
        )
        response = await self._client.aio.models.generate_content(
            model=self._text_model,
            contents=prompt,
        )
        parsed = self._safe_load_json(response.text or "", {})
        model_returned_raw = str(parsed.get("window_key_color", "")).strip()
        normalized_choice = self._normalize_key_color_choice(model_returned_raw)
        if normalized_choice is not None:
            return normalized_choice, normalized_choice
        return self.WINDOW_KEY_COLORS[0], model_returned_raw

    async def _select_best_key_color(
        self,
        image_bytes: bytes,
        preferred_key_color: str | None = None,
    ) -> dict[str, Any]:
        """Pick the best key color by evaluating all supported candidates on the generated image."""
        image_data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
        image_area = self.BACKGROUND_WIDTH * self.BACKGROUND_HEIGHT
        best_color = self.WINDOW_KEY_COLORS[0]
        best_count = 0
        best_windows: list[dict[str, Any]] = []
        best_score = float("-inf")
        candidate_scores: list[dict[str, Any]] = []

        for color in self.WINDOW_KEY_COLORS:
            outlined = await outline_windows_from_image(
                image_data_uri,
                color,
                allow_key_fallback=False,
            )
            windows = outlined.get("windows", [])
            scoring_windows = outlined.get("scoring_windows", [])
            window_count = len(scoring_windows)

            window_areas = sorted(
                (
                    int(win.get("width", 0)) * int(win.get("height", 0))
                    for win in scoring_windows
                ),
                reverse=True,
            )
            top_window_areas = window_areas[: self.KEY_COLOR_TOP_WINDOW_COUNT]
            total_area = sum(window_areas)
            top_window_area = sum(top_window_areas)
            largest_area = window_areas[0] if window_areas else 0
            has_absurdly_large_box = largest_area > image_area * self.KEY_COLOR_ABSURD_BOX_AREA_RATIO

            score = float(top_window_area) - float(largest_area * 0.35)
            if window_count == 0:
                score = -2000.0
            elif window_count < 4 or window_count > 30:
                score *= 0.6

            if has_absurdly_large_box:
                score = -10000.0

            if (
                preferred_key_color
                and color == preferred_key_color
                and window_count > 0
                and not has_absurdly_large_box
            ):
                score += self.KEY_COLOR_MODEL_MATCH_BONUS

            candidate_scores.append(
                {
                    "key_color": color,
                    "window_count": window_count,
                    "windows": scoring_windows,
                    "total_area": total_area,
                    "top_window_area": top_window_area,
                    "top_window_areas": top_window_areas,
                    "largest_area": largest_area,
                    "has_absurdly_large_box": has_absurdly_large_box,
                    "score": score,
                }
            )

            if score > best_score:
                best_score = score
                best_color = color
                best_count = window_count
                best_windows = windows  # padded windows kept for gameplay

        return {
            "selected_key_color": best_color,
            "selected_window_count": best_count,
            "selected_windows": best_windows,
            "candidate_scores": candidate_scores,
        }

    async def generate_background(
        self,
        theme: str,
        on_attempt: BackgroundAttemptCallback | None = None,
    ) -> GeneratedBackground:
        """Generate a background image and return a data-URI plus key-color metadata."""
        try:
            last_fixed: bytes | None = None
            last_key_color = self.WINDOW_KEY_COLORS[0]
            last_decision: dict[str, Any] = {
                "supported_key_colors": list(self.WINDOW_KEY_COLORS),
                "message": "No attempts completed",
            }
            key_color_options = self._window_key_color_list_text()
            for attempt in range(1, self.BACKGROUND_MAX_RETRIES + 1):
                chosen_key_color, model_returned_key_color = await self._choose_key_color_for_theme(theme)
                chosen_key_color_label = self._window_key_color_label(chosen_key_color)
                prompt = (
                    f"A detailed game background scene for a whack-a-mole monster game. "
                    f"Theme: {theme}. "
                    "The scene must show architecture with clearly visible rectangular windows/openings "
                    "that are EMPTY and unobstructed. "
                    f"Use ONLY this mask color for ALL window/opening interiors: {chosen_key_color} ({chosen_key_color_label}). "
                    f"Supported mask color list for reference: {key_color_options}. "
                    "That chosen mask color will be removed in post-processing, so it must appear ONLY inside openings AND as a solid outer border. "
                    f"Paint a solid, unbroken border of exactly {chosen_key_color} ({chosen_key_color_label}) that is 10–15 pixels wide around the entire outer edge of the image. "
                    "Do not use the chosen mask color anywhere else in the image outside of window interiors and the outer border. "
                    "Do not use any of the other supported mask colors anywhere in the image. "
                    "No gradients or texture inside those colored openings. "
                    "Do not include monsters, creatures, people, silhouettes, faces, or characters inside windows/openings. "
                    "Do not include text or UI elements. Cartoon/illustrated style, vivid colours. "
                    f"Attempt variation {attempt}: emphasize clean, empty opening interiors suitable for sprite pop-outs."
                )
                image_bytes, _ = await self._generate_image_bytes(prompt, aspect_ratio="16:9")
                fixed = self._resize_background_to_canvas(
                    image_bytes,
                    self.BACKGROUND_WIDTH,
                    self.BACKGROUND_HEIGHT,
                )
                last_fixed = fixed

                has_occupied_windows = await self._has_occupied_windows(fixed, "image/png")
                selection = await self._select_best_key_color(
                    fixed,
                    preferred_key_color=(
                        model_returned_key_color
                        if model_returned_key_color in self.WINDOW_KEY_COLORS
                        else None
                    ),
                )
                selected_key_color = str(selection.get("selected_key_color") or self.WINDOW_KEY_COLORS[0])
                selected_window_count = int(selection.get("selected_window_count") or 0)
                last_key_color = selected_key_color
                attempt_image_url = "data:image/png;base64," + base64.b64encode(fixed).decode()
                last_decision = {
                    "supported_key_colors": list(self.WINDOW_KEY_COLORS),
                    "model_returned_key_color": model_returned_key_color,
                    "model_returned_supported": model_returned_key_color in self.WINDOW_KEY_COLORS,
                    "prompt_requested_key_color": chosen_key_color,
                    "selected_key_color": selected_key_color,
                    "selected_window_count": selected_window_count,
                    "has_occupied_windows": has_occupied_windows,
                    "attempt": attempt,
                    "candidate_scores": selection.get("candidate_scores", []),
                }

                if on_attempt is not None:
                    attempt_status = (
                        "success"
                        if (not has_occupied_windows and selected_window_count > 0)
                        else "retrying"
                    )
                    try:
                        await on_attempt(
                            {
                                "attempt": attempt,
                                "max_attempts": self.BACKGROUND_MAX_RETRIES,
                                "status": attempt_status,
                                "url": attempt_image_url,
                                "window_key_color": selected_key_color,
                                "color_decision": dict(last_decision),
                            }
                        )
                    except Exception as exc:
                        logger.warning("Attempt callback failed: %s", exc)

                if not has_occupied_windows and selected_window_count > 0:
                    return {
                        "image_url": attempt_image_url,
                        "window_key_color": selected_key_color,
                        "color_decision": last_decision,
                    }

                logger.warning(
                    "Background generation attempt %s/%s failed validation (occupied_windows=%s, selected_key=%s, selected_windows=%s); retrying",
                    attempt,
                    self.BACKGROUND_MAX_RETRIES,
                    has_occupied_windows,
                    selected_key_color,
                    selected_window_count,
                )

            logger.warning(
                "Unable to generate a background with empty windows after %s attempts",
                self.BACKGROUND_MAX_RETRIES,
            )
            return {
                "image_url": "",
                "window_key_color": last_key_color,
                "color_decision": last_decision,
            }
        except Exception as exc:
            logger.warning("Strict background generation failed; returning no background: %s", exc)
            return {
                "image_url": "",
                "window_key_color": self.WINDOW_KEY_COLORS[0],
                "color_decision": {
                    "supported_key_colors": list(self.WINDOW_KEY_COLORS),
                    "message": f"Background generation failed: {exc}",
                },
            }

    async def extract_bounding_boxes(self, image_url: str) -> list[dict[str, Any]]:
        """Use Gemini vision to detect window bounding boxes in an image."""
        # Fetch image bytes when given a URL
        if image_url.startswith("http"):
            async with httpx.AsyncClient() as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                image_bytes = resp.content
                mime = resp.headers.get("content-type", "image/png").split(";")[0]
        else:
            # Assume data-URI: data:<mime>;base64,<data>
            header, encoded = image_url.split(",", 1)
            mime = header.split(":")[1].split(";")[0]
            image_bytes = base64.b64decode(encoded)

        image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime)
        prompt = (
            "Identify all EMPTY rectangular windows or openings in this image that a monster "
            "could pop out of. Return only openings that are clearly visible and not occupied by "
            "any person, animal, creature, object, or decoration. "
            "Ignore doors, signs, reflections, railings, and decorative non-openings. "
            "Respond with ONLY a JSON array where each item has keys "
            '"x", "y", "width", "height". Use integer pixel values with origin at top-left, '
            "no decimals, no extra keys, no markdown. Use tight bounds around the opening interior. "
            "If none are found, return []."
        )
        response = await self._client.aio.models.generate_content(
            model=self._text_model,
            contents=[image_part, prompt],
        )
        candidates = self._safe_load_json(response.text or "", [])
        if not isinstance(candidates, list) or not candidates:
            return []

        refine_prompt = (
            "You are refining candidate window boxes for gameplay snapping. "
            "Adjust each candidate to the nearest EMPTY window/opening interior in the image. "
            "Keep boxes axis-aligned and tight. Remove candidates that are not true empty windows. "
            "Return ONLY a JSON array with keys x,y,width,height as integers. "
            f"Candidates: {json.dumps(candidates)}"
        )
        refined_response = await self._client.aio.models.generate_content(
            model=self._text_model,
            contents=[image_part, refine_prompt],
        )
        refined = self._safe_load_json(refined_response.text or "", candidates)
        return refined if isinstance(refined, list) else candidates

    async def generate_sprite(self, character_description: str) -> str:
        """Generate a monster sprite with background removed and return a data-URI."""
        prompt = (
            f"A cartoon monster character: {character_description}. "
            "Plain white background, full-body portrait, facing forward, "
            "vivid colours, game sprite style."
        )
        image_bytes, _ = await self._generate_image_bytes(prompt, aspect_ratio="1:1")
        stripped = self._strip_white_background(image_bytes)
        b64 = base64.b64encode(stripped).decode()
        return "data:image/png;base64," + b64
