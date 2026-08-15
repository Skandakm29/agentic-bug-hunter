import time
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("backend.ai.memory.working")


class WorkingMemory:
    """Ephemeral, low-latency, execution-scoped working memory with LRU eviction."""

    def __init__(self, max_capacity: int = 50):
        self.max_capacity = max_capacity
        self.store: Dict[str, Any] = {}
        self.access_times: Dict[str, float] = {}

    def write(self, key: str, value: Any):
        if len(self.store) >= self.max_capacity and key not in self.store:
            self._evict_lru()
        self.store[key] = value
        self.access_times[key] = time.time()
        logger.debug(f"WorkingMemory write: '{key}'")

    def read(self, key: str) -> Optional[Any]:
        if key in self.store:
            self.access_times[key] = time.time()
            return self.store[key]
        return None

    def delete(self, key: str):
        if key in self.store:
            del self.store[key]
            del self.access_times[key]

    def _evict_lru(self):
        if not self.access_times:
            return
        lru_key = min(self.access_times, key=self.access_times.get)
        logger.info(f"WorkingMemory capacity full. Evicting LRU key: '{lru_key}'")
        self.delete(lru_key)

    def clear(self):
        self.store.clear()
        self.access_times.clear()
