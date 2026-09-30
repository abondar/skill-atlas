"""Error hierarchy. Each error class carries the CLI exit code from SPEC section 3.3."""


class AtlasError(Exception):
    exit_code = 1


class UsageError(AtlasError):
    exit_code = 2


class AccessError(AtlasError):
    exit_code = 3


class NetworkError(AtlasError):
    exit_code = 4


class RateLimitError(NetworkError):
    pass


class StoreError(AtlasError):
    exit_code = 5
