"""findmypylibrary — describe a use case in plain English, get a ranked shortlist
of real PyPI packages.

    import findmypylibrary
    for package, score in findmypylibrary.search("parse pdf files", top_n=5):
        print(package["name"], score)
"""

from __future__ import annotations

__version__ = "0.2.0"

from .rank import search  # noqa: E402  (must follow __version__: fetch imports it)

__all__ = ["__version__", "search"]
