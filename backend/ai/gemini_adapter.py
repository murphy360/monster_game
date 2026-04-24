"""Concrete AIGenerator implementation backed by Google Gemini.

Uses:
- ``gemini-2.0-flash`` for text (level config generation).
- ``gemini-2.0-flash`` with vision for bounding-box extraction.
- ``imagen-3.0-generate-002`` for image generation (backgrounds & sprites).

The adapter reads ``GEMINI_API_KEY`` from the environment (or a .env file
loaded by python-dotenv).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

import httpx
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

from .base import AIGenerator

load_dotenv()

logger = logging.getLogger(__name__)


class GeminiAdapter(AIGenerator):
    """AIGenerator implementation using Google Gemini models."""

    # Text / vision model
    TEXT_MODEL = "gemini-2.0-flash"
    # Image generation model
    IMAGE_MODEL = "imagen-3.0-generate-002"

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY environment variable is not set. "
                "Create a .env file in the backend/ directory with GEMINI_API_KEY=<your key>."
            )
        self._client = genai.Client(api_key=api_key)

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
            "    ... (6-8 windows total, coordinates fit a 1280x720 canvas)\n"
            "  ],\n"
            '  "monster_descriptions": ["<description for window 1>", ...]\n'
            "}"
        )
        response = await self._client.aio.models.generate_content(
            model=self.TEXT_MODEL,
            contents=prompt,
        )
        raw = response.text.strip()
        # Strip optional markdown fences
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)

    async def generate_background(self, theme: str) -> str:
        """Generate a background image with Imagen and return a data-URI."""
        result = await self._client.aio.models.generate_images(
            model=self.IMAGE_MODEL,
            prompt=(
                f"A detailed game background scene for a whack-a-mole monster game. "
                f"Theme: {theme}. "
                "The scene shows a building facade or landscape with clearly visible "
                "rectangular windows or openings. Cartoon/illustrated style, vivid colours. "
                "No text, no UI elements."
            ),
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="16:9",
            ),
        )
        image_bytes: bytes = result.generated_images[0].image.image_bytes
        b64 = base64.b64encode(image_bytes).decode()
        return f"data:image/png;base64,{b64}"

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
            "Identify all rectangular windows or openings in this image that a monster "
            "could pop out of. For each window return a JSON array entry with keys "
            '"x", "y", "width", "height" (integer pixel values, origin at top-left). '
            "Return ONLY the JSON array, no explanation or markdown."
        )
        response = await self._client.aio.models.generate_content(
            model=self.TEXT_MODEL,
            contents=[image_part, prompt],
        )
        raw = response.text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)

    async def generate_sprite(self, character_description: str) -> str:
        """Generate a monster sprite with Imagen and return a data-URI."""
        result = await self._client.aio.models.generate_images(
            model=self.IMAGE_MODEL,
            prompt=(
                f"A cartoon monster character: {character_description}. "
                "Transparent background, full-body portrait, facing forward, "
                "vivid colours, game sprite style."
            ),
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1",
            ),
        )
        image_bytes: bytes = result.generated_images[0].image.image_bytes
        b64 = base64.b64encode(image_bytes).decode()
        return f"data:image/png;base64,{b64}"
