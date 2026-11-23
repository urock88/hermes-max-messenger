"""A small asynchronous client for the MAX Bot API (https://dev.max.ru/docs-api).

Nothing here imports Hermes: the client can be tested and reused on its own.
"""

from .client import DEFAULT_API_BASE, MaxClient
from .errors import MaxApiError

__all__ = ["DEFAULT_API_BASE", "MaxApiError", "MaxClient"]
