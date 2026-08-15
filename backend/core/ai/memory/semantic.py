import math
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("backend.ai.memory.semantic")


class SemanticMemory:
    """Long-term reusable semantic engineering knowledge query store using similarity heuristics."""

    def __init__(self):
        # Maps text concepts to mock vector representations for simple local evaluations
        self.kb: List[Tuple[str, List[float], Dict[str, Any]]] = []

    def _tokenize_and_embed(self, text: str) -> List[float]:
        # Simple word hashing to generate deterministic mock vectors for testing
        words = text.lower().split()
        vector = [0.0] * 8
        for w in words:
            idx = sum(ord(c) for c in w) % 8
            vector[idx] += 1.0
        
        # Normalize vector
        magnitude = math.sqrt(sum(v * v for v in vector))
        if magnitude > 0:
            vector = [v / magnitude for v in vector]
        return vector

    def add_fact(self, fact_text: str, metadata: Dict[str, Any]):
        vector = self._tokenize_and_embed(fact_text)
        self.kb.append((fact_text, vector, metadata))
        logger.info(f"Added concept to SemanticMemory: {fact_text[:50]}...")

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        query_vector = self._tokenize_and_embed(query)
        scored = []
        for text, vec, meta in self.kb:
            # Cosine similarity calculation
            score = sum(q * v for q, v in zip(query_vector, vec))
            scored.append((score, text, meta))
        
        # Sort desc
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"text": text, "score": round(score, 4), "metadata": meta}
            for score, text, meta in scored[:top_k]
        ]
