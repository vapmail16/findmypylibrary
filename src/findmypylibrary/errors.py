"""Exceptions whose message tells the user what to do next. They live apart from
fetch.py so the search path can catch them without importing the HTTP stack."""

from __future__ import annotations


class SnapshotError(Exception):
    """The local snapshot is missing, corrupt, empty or from another schema version."""


class FetchError(Exception):
    """A download needed for refresh failed."""
