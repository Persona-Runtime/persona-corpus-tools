"""Errors raised for unusable, but otherwise verified, source content."""


class AdapterParseError(ValueError):
    """An adapter could not safely produce canonical records from its input."""
