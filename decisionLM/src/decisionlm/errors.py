"""Exceptions shared by both tiers and the HTTP layer (which maps each to a status code)."""


class ScoringError(Exception):
    """Raised for any condition where we refuse to guess and must surface a clear error (HTTP 400)."""


class PromptTooLongError(ScoringError):
    """The prompt does not fit in the context window (HTTP 413)."""


class ModelLoadError(ScoringError):
    """The model file is missing or failed to load: a server problem, not a bad request (HTTP 503)."""
