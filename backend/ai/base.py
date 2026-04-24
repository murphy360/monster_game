"""Abstract base class defining the AI generator interface.

This module provides a model-agnostic interface so that any AI provider
(Gemini, OpenAI, Anthropic, etc.) can be swapped in by implementing this
interface without touching the rest of the application.
"""

from abc import ABC, abstractmethod
from typing import Any


class AIGenerator(ABC):
    """Abstract interface for AI-powered game content generation."""

    @abstractmethod
    async def generate_level_config(self, theme: str) -> dict[str, Any]:
        """Generate a level configuration including window layout metadata.

        Args:
            theme: A text description of the level theme (e.g. "haunted house").

        Returns:
            A dict containing at minimum:
              - ``title`` (str): Display name for the level.
              - ``windows`` (list[dict]): List of window objects, each with
                ``id``, ``x``, ``y``, ``width``, and ``height`` fields.
              - ``monster_descriptions`` (list[str]): Text descriptions for
                sprites to generate, one per window.
        """

    @abstractmethod
    async def generate_background(self, theme: str) -> str:
        """Generate or retrieve a background image for the given theme.

        Args:
            theme: A text description of the level theme.

        Returns:
            A URL or base-64 data-URI string for the background image.
        """

    @abstractmethod
    async def extract_bounding_boxes(self, image_url: str) -> list[dict[str, Any]]:
        """Use vision AI to extract window / opening bounding boxes from an image.

        Args:
            image_url: Public URL (or data-URI) of the background image.

        Returns:
            A list of dicts, each containing ``x``, ``y``, ``width``, and
            ``height`` keys (all in pixels) measured in the source image's
            coordinate space (top-left origin). For generated backgrounds in
            this project, coordinates should align with a 1280x720 image-space
            board contract unless the API returns explicit board dimensions.
        """

    @abstractmethod
    async def generate_sprite(self, character_description: str) -> str:
        """Generate a monster sprite image for the given description.

        Args:
            character_description: Text description of the character/monster.

        Returns:
            A URL or base-64 data-URI string for the sprite image.
        """
