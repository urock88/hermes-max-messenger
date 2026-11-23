"""MAX API failures, classified once so callers never parse error text."""

from __future__ import annotations

# The classification Hermes uses for ``SendResult.error_kind`` (gateway.platforms.base.SEND_ERROR_KINDS).
TOO_LONG = "too_long"
BAD_FORMAT = "bad_format"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
RATE_LIMITED = "rate_limited"
TRANSIENT = "transient"
UNKNOWN = "unknown"


class MaxApiError(Exception):
    """A refused or failed MAX API call.

    ``status`` is the HTTP status (0 for a network failure), ``code`` the API's machine code
    (``attachment.not.ready``, ``chat.denied`` ...), ``message`` its human text. Some MAX methods
    answer HTTP 200 with ``{"success": false, "code", "message"}``; the client raises this error for
    those too, with ``status`` 200.
    """

    def __init__(self, status: int, code: str = "", message: str = "", *,
                 retry_after: float | None = None, method: str = "", path: str = ""):
        self.status = int(status or 0)
        self.code = code or ""
        self.message = message or ""
        self.retry_after = retry_after
        self.method = method
        self.path = path
        where = f"{method} {path}".strip()
        detail = ": ".join(part for part in (self.code, self.message) if part)
        super().__init__(f"MAX API {where} -> {self.status or 'network'} {detail}".strip())

    @property
    def is_auth(self) -> bool:
        """The token is wrong or revoked: nothing will work until a person fixes it."""
        return self.status == 401

    @property
    def is_not_ready(self) -> bool:
        """An uploaded attachment is still being processed (retry later)."""
        return self.code == "attachment.not.ready"

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429

    @property
    def is_transient(self) -> bool:
        """A network failure or a server-side error: the same request may succeed later."""
        return self.status == 0 or self.status >= 500 or self.is_rate_limited

    @property
    def kind(self) -> str:
        """One of Hermes' send-error kinds."""
        text = f"{self.code} {self.message}".lower()
        if self.is_rate_limited:
            return RATE_LIMITED
        if self.status == 0 or self.status >= 500:
            return TRANSIENT
        if self.status == 403 or "denied" in text or "blocked" in text:
            # A person stopped or blocked the bot, or the bot is not a member of the chat.
            return FORBIDDEN
        if self.status == 404 or "not.found" in text or "not found" in text:
            return NOT_FOUND
        if "too.long" in text or "too long" in text or "length" in text:
            return TOO_LONG
        if self.status == 400 and ("format" in text or "markup" in text or "parse" in text):
            return BAD_FORMAT
        return UNKNOWN
