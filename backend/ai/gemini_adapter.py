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
import secrets
from typing import Any

from PIL import Image

import httpx
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

from .base import AIGenerator, GeneratedBackground

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
    BACKGROUND_MAX_RETRIES = 6
    WINDOW_KEY_COLORS = (
        "#00FF00",
        "#FF00FF",
        "#00FFFF",
        "#FFFF00",
        "#FF5F00",
        "#00A6FF",
    )

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

    async def _has_key_color_conflict(
        self,
        image_bytes: bytes,
        mime_type: str,
        key_color: str,
    ) -> bool:
        """Return True when the chosen key color appears outside intended openings."""
        image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        prompt = (
            f"Check whether the exact color {key_color}, or obviously near-identical anti-aliased variants, "
            "appears anywhere outside the interior of intended architectural windows/openings in this scene. "
            "Treat use of that color on leaves, sky, decorations, trim, reflections, props, ground, or water as a conflict. "
            "Ignore only thin anti-aliased edge pixels immediately touching a valid window/opening interior. "
            "Respond with ONLY JSON object: {\"has_key_color_conflict\": true|false}."
        )
        response = await self._client.aio.models.generate_content(
            model=self._text_model,
            contents=[image_part, prompt],
        )
        parsed = self._safe_load_json(response.text or "", {"has_key_color_conflict": False})
        return bool(parsed.get("has_key_color_conflict", False))

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
    def _pick_window_key_color(cls) -> str:
        """Choose a reserved chroma-key color unlikely to appear naturally."""
        return secrets.choice(cls.WINDOW_KEY_COLORS)

    async def generate_background(self, theme: str) -> GeneratedBackground:
        """Generate a background image and return a data-URI plus key-color metadata."""
        try:
            last_fixed: bytes | None = None
            last_key_color = self._pick_window_key_color()
            for attempt in range(1, self.BACKGROUND_MAX_RETRIES + 1):
                key_color = self._pick_window_key_color()
                last_key_color = key_color
                prompt = (
                    f"A detailed game background scene for a whack-a-mole monster game. "
                    f"Theme: {theme}. "
                    "The scene must show architecture with clearly visible rectangular windows/openings "
                    "that are EMPTY and unobstructed. "
                    f"Fill every window/opening interior with SOLID flat color {key_color}. "
                    f"Do not use {key_color} anywhere else in the image. "
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
                has_key_color_conflict = await self._has_key_color_conflict(
                    fixed,
                    "image/png",
                    key_color,
                )
                if not has_occupied_windows and not has_key_color_conflict:
                    b64 = base64.b64encode(fixed).decode()
                    return {
                        "image_url": "data:image/png;base64," + b64,
                        "window_key_color": key_color,
                    }

                logger.warning(
                    "Background generation attempt %s/%s failed validation (occupied_windows=%s, key_color_conflict=%s); retrying",
                    attempt,
                    self.BACKGROUND_MAX_RETRIES,
                    has_occupied_windows,
                    has_key_color_conflict,
                )

            logger.warning(
                "Unable to generate a background with empty windows after %s attempts",
                self.BACKGROUND_MAX_RETRIES,
            )
            b64 = base64.b64encode(last_fixed or b"").decode()
            return {
                "image_url": "data:image/png;base64," + b64,
                "window_key_color": last_key_color,
            }
        except Exception as exc:
            logger.warning("Strict background generation failed; using relaxed fallback: %s", exc)
            key_color = self._pick_window_key_color()
            relaxed_prompt = (
                f"A detailed game background scene for a whack-a-mole monster game. "
                f"Theme: {theme}. "
                "The scene shows a building facade or landscape with clearly visible rectangular windows or openings. "
                f"Fill every window/opening interior with SOLID flat color {key_color}, and do not use {key_color} elsewhere. "
                "No text, no UI elements. Cartoon/illustrated style, vivid colours."
            )
            image_bytes, _ = await self._generate_image_bytes(relaxed_prompt, aspect_ratio="16:9")
            fixed = self._resize_background_to_canvas(
                image_bytes,
                self.BACKGROUND_WIDTH,
                self.BACKGROUND_HEIGHT,
            )
            b64 = base64.b64encode(fixed).decode()
            return {
                "image_url": "data:image/png;base64," + b64,
                "window_key_color": key_color,
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
