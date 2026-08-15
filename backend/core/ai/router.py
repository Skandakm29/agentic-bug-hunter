import logging
from typing import Dict, Any

from backend.core.config import settings

logger = logging.getLogger("backend.ai.router")


class ModelRoutingEngine:
    """Routes validation tasks to specific models based on reasoning complexity requirements."""

    def __init__(self):
        # Model IDs are sourced from Settings rather than hard-coded here, so
        # there is a single place to update when a hosted model is retired.
        fast_model      = settings.GROQ_MODEL
        reasoning_model = settings.GROQ_REASONING_MODEL

        self.routes: Dict[str, Dict[str, Any]] = {
            "classification": {
                "model": fast_model,
                "temperature": 0.0,
                "max_tokens": 128
            },
            "static_validation": {
                "model": fast_model,
                "temperature": 0.1,
                "max_tokens": 1024
            },
            "complex_fix": {
                # Route complex repairs to the larger reasoning model.
                "model": reasoning_model,
                "temperature": 0.2,
                "max_tokens": 2048
            }
        }

    def route_task(self, task_type: str) -> Dict[str, Any]:
        route = self.routes.get(task_type.lower(), self.routes["static_validation"])
        logger.info(f"Routed task '{task_type}' to model '{route['model']}' (temp={route['temperature']})")
        return route
