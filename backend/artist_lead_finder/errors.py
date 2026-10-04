"""Errors whose text is written for the user and is safe to show as is."""


class UserError(ValueError):
    """A ValueError the RPC layer passes to the UI unchanged; other ValueErrors may carry
    internal details (paths, parser output) and are replaced by a generic message."""
