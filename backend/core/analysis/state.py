import os
import json
import time
import logging
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("backend.analysis.state")


class ExecutionState(BaseModel):
    """Execution state tracker for tracking lifecycle progress and checkpoints."""
    execution_id: str
    state: str = "CREATED"  # CREATED, PLANNING, EXECUTING, VALIDATION, COMPLETED, FAILED
    current_step: int = 0
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    errors: Dict[str, str] = Field(default_factory=dict)


class StateManager:
    """Manages serialization checkpoints and lifecycle recovery."""

    def __init__(self, storage_dir: str = "./backend/storage/checkpoints/"):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_path(self, execution_id: str) -> str:
        return os.path.join(self.storage_dir, f"{execution_id}.json")

    def transition(self, execution: ExecutionState, new_state: str):
        old_state = execution.state
        execution.state = new_state
        logger.info(f"Execution '{execution.execution_id}' transitioned: {old_state} -> {new_state}")
        self.save_checkpoint(execution)

    def save_checkpoint(self, execution: ExecutionState):
        path = self._get_path(execution.execution_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(execution.model_dump_json(indent=2))
            logger.debug(f"Saved execution checkpoint: {path}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint '{execution.execution_id}': {e}")

    def load_checkpoint(self, execution_id: str) -> Optional[ExecutionState]:
        path = self._get_path(execution_id)
        if not os.path.exists(path):
            logger.warning(f"No checkpoint found: {path}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ExecutionState(**data)
        except Exception as e:
            logger.error(f"Failed to load checkpoint '{execution_id}': {e}")
            return None
