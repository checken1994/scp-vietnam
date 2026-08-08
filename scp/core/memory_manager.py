"""[V104.34 #34] memory_manager — wraps SmartCache via get_smart_cache().

TẠI SAO: Behavioral test expects this module to use the factory function
(not the deprecated singleton accessor).
The module was previously deleted during a refactor but the test + expected
behavior remain. This restore provides a thin memory-manager facade.

DNA SCP: If a test verifies a behavior contract, the contract must be honored
even after refactors. Either restore the module OR delete the test — but
don't leave the test failing silently (that hides real regressions).
"""
from __future__ import annotations

from scp.core.smart_cache import get_smart_cache, SmartCache


class MemoryManager:
    """Thin wrapper around the global SmartCache instance.

    Uses get_smart_cache() (factory) instead of the deprecated singleton pattern
    (deprecated singleton) per V104.34 #34 fix.
    """

    def __init__(self):
        # [V104.34 #34] Use factory function, not deprecated singleton.
        self._cache: SmartCache = get_smart_cache()

    def get(self, key: str, default=None):
        """Retrieve a cached value by key."""
        return self._cache.get(key, default)

    def set(self, key: str, value, ttl: int | None = None) -> None:
        """Store a value in cache with optional TTL."""
        self._cache.set(key, value, ttl)

    def delete(self, key: str) -> bool:
        """Delete a key from cache. Returns True if key existed."""
        return self._cache.delete(key)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def stats(self) -> dict:
        """Return cache statistics."""
        return self._cache.stats() if hasattr(self._cache, "stats") else {}


__all__ = ["MemoryManager", "get_smart_cache"]
