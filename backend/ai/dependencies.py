"""FastAPI dependency injection for the AIGenerator interface.

Swap out ``GeminiAdapter`` for any other ``AIGenerator`` implementation here
without touching the route handlers.
"""

from functools import lru_cache

from .base import AIGenerator
from .gemini_adapter import GeminiAdapter


@lru_cache(maxsize=1)
def get_ai_generator() -> AIGenerator:
    """Return the singleton AIGenerator implementation.

    Currently wired to :class:`GeminiAdapter`.  Replace with any class that
    implements :class:`AIGenerator` to switch AI providers.
    """
    return GeminiAdapter()
