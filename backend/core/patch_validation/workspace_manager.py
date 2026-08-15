import os
import shutil
import tempfile
import logging
from backend.core.patch_validation.validation_models import Workspace
from backend.core.patch_validation.exceptions import WorkspaceError

logger = logging.getLogger("backend.patch_validation.workspace")


class WorkspaceManager:
    """Manages creation, population, and cleanup of isolated patch workspaces."""

    def __init__(self, original_path: str, workspace_type: str = "temp_dir"):
        self.original_path = os.path.abspath(original_path)
        self.workspace_type = workspace_type.lower()
        self._temp_dir = None
        self.workspace_path = None

    def __enter__(self) -> Workspace:
        try:
            if self.workspace_type == "temp_dir":
                # Create a temporary directory in workspace
                self._temp_dir = tempfile.TemporaryDirectory(prefix="argus_val_")
                self.workspace_path = os.path.abspath(self._temp_dir.name)
                
                # Copy entire original codebase directory to the workspace
                if os.path.isdir(self.original_path):
                    shutil.copytree(
                        self.original_path, 
                        self.workspace_path, 
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "venv", ".venv", "build")
                    )
                else:
                    # If original_path is a file, copy parent directory if possible, or just the file
                    parent = os.path.dirname(self.original_path)
                    if parent and os.path.exists(parent):
                        shutil.copytree(
                            parent, 
                            self.workspace_path, 
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", "venv", ".venv", "build")
                        )
                    else:
                        shutil.copy2(self.original_path, self.workspace_path)
                
                logger.info(f"Created temp_dir workspace at {self.workspace_path}")
            elif self.workspace_type == "git_worktree":
                # Fallback to copy-based temp directory for safety and platform independence
                self._temp_dir = tempfile.TemporaryDirectory(prefix="argus_git_")
                self.workspace_path = os.path.abspath(self._temp_dir.name)
                if os.path.isdir(self.original_path):
                    shutil.copytree(
                        self.original_path, 
                        self.workspace_path, 
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "venv", ".venv", "build")
                    )
                else:
                    shutil.copy2(self.original_path, self.workspace_path)
                logger.info(f"Created git_worktree fallback workspace at {self.workspace_path}")
            elif self.workspace_type == "none":
                # In-place validation (original repository is modified)
                self.workspace_path = self.original_path
                logger.warning("Using in-place validation (no isolation). Original files will be modified!")
            else:
                raise ValueError(f"Unknown workspace isolation type: {self.workspace_type}")

            return Workspace(
                path=self.workspace_path,
                original_path=self.original_path,
                workspace_type=self.workspace_type
            )
        except Exception as e:
            logger.error(f"Workspace creation failed: {e}")
            raise WorkspaceError(f"Failed to initialize workspace: {e}") from e

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.workspace_type != "none":
            try:
                if self._temp_dir:
                    self._temp_dir.cleanup()
                    logger.info(f"Cleaned up workspace at {self.workspace_path}")
            except Exception as e:
                logger.warning(f"Error during workspace cleanup: {e}")
