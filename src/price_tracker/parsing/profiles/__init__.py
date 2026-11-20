"""Importing this package registers all built-in profiles."""

from price_tracker.parsing.profiles import aldi, generic, lidl, rewe

__all__ = ["aldi", "generic", "lidl", "rewe"]
