import os
import logging

logger = logging.getLogger("backend.patch_validation.rollback")


class RollbackManager:
    """Manages file backup and restoration to guarantee atomic changes."""

    def __init__(self) -> None:
        self._backups = {}

    def backup_file(self, file_path: str) -> None:
        """
        Backup the target file content in-memory.

        Parameters
        ----------
        file_path : Absolute path to target file.
        """
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    self._backups[file_path] = f.read()
                logger.debug(f"Backed up file: {file_path}")
            except Exception as e:
                logger.warning(f"Could not create backup of file {file_path}: {e}")

    def rollback_all(self) -> None:
        """Restore all backed-up files back to their original state."""
        for file_path, original_content in self._backups.items():
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(original_content)
                logger.info(f"Restored file content for: {file_path}")
            except Exception as e:
                logger.error(f"Failed to restore file {file_path} during rollback: {e}")
        self._backups.clear()
