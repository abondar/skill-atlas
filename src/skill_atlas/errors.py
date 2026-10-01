"""Error hierarchy. Each error class carries the CLI exit code from SPEC section 3.3."""


class AtlasError(Exception):
    exit_code = 1


class UsageError(AtlasError):
    exit_code = 2


class AccessError(AtlasError):
    exit_code = 3


class TokenError(AccessError):
    """The host rejected the token: every further request fails the same way."""


class NetworkError(AtlasError):
    exit_code = 4


class RateLimitError(NetworkError):
    def __init__(
        self, message: str, *, reset_at: float | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        # Primary limit: epoch seconds of the reset. Secondary limit: seconds to wait.
        self.reset_at = reset_at
        self.retry_after = retry_after


class StoreError(AtlasError):
    exit_code = 5


class Interrupted(AtlasError):
    """The user stopped an organization scan (Ctrl+C, Stop, `x`)."""

    exit_code = 130


# Not an exception: an organization scan that finished with failed repositories.
PARTIAL_EXIT_CODE = 6
