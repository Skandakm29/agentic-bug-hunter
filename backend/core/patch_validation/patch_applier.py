import os
import logging
from backend.core.patch_validation.exceptions import PatchApplyError

logger = logging.getLogger("backend.patch_validation.patch_applier")


class PatchApplier:
    """Applies patch candidates onto target source files in a workspace."""

    def apply_patch(self, workspace_path: str, file_path: str, candidate) -> None:
        """
        Apply a patch candidate to the specified file path in the workspace.

        Parameters
        ----------
        workspace_path : Path of the workspace.
        file_path : Relpath or abspath of target file.
        candidate : PatchCandidate object.
        """
        target_file = os.path.join(workspace_path, file_path)
        if not os.path.exists(target_file):
            # Try to resolve relative path if original had parent pathing
            resolved = False
            for root, _, files in os.walk(workspace_path):
                for f in files:
                    if f == os.path.basename(file_path):
                        target_file = os.path.join(root, f)
                        resolved = True
                        break
                if resolved:
                    break
            
            if not resolved:
                raise PatchApplyError(f"Target file {file_path} not found in workspace.")

        try:
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            raise PatchApplyError(f"Failed to read target file {target_file}: {e}")

        # Try unified diff application first
        applied = False
        error_msgs = []
        
        if candidate.unified_diff:
            try:
                content_patched = self._apply_diff(content, candidate.unified_diff)
                applied = True
                logger.info(f"Applied unified diff successfully to {target_file}")
            except Exception as e:
                error_msgs.append(f"Diff application error: {e}")

        # Fallback to block replacement
        if not applied and candidate.original_code:
            try:
                content_patched = self._apply_block_replace(
                    content, candidate.original_code, candidate.patched_code
                )
                applied = True
                logger.info(f"Applied block replacement successfully to {target_file}")
            except Exception as e:
                error_msgs.append(f"Block replacement error: {e}")

        if not applied:
            msg = "Failed to apply patch candidates. Details: " + " | ".join(error_msgs)
            raise PatchApplyError(msg)

        try:
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(content_patched)
        except Exception as e:
            raise PatchApplyError(f"Failed to write patched contents to {target_file}: {e}")

    def _apply_diff(self, file_content: str, diff_text: str) -> str:
        lines = file_content.splitlines(keepends=True)
        diff_lines = diff_text.splitlines()
        
        hunks = []
        current_hunk = None
        
        for dl in diff_lines:
            if dl.startswith("@@ "):
                if current_hunk:
                    hunks.append(current_hunk)
                parts = dl.split(" ")
                if len(parts) < 3:
                    continue
                old_info = parts[1][1:].split(",")
                old_start = int(old_info[0]) - 1
                old_len = int(old_info[1]) if len(old_info) > 1 else 1
                
                current_hunk = {
                    "old_start": max(0, old_start),
                    "old_len": old_len,
                    "lines": []
                }
            elif current_hunk:
                if dl.startswith("+") or dl.startswith("-") or dl.startswith(" "):
                    current_hunk["lines"].append(dl)
                    
        if current_hunk:
            hunks.append(current_hunk)
            
        if not hunks:
            raise ValueError("No valid hunks found in diff.")
            
        hunks.sort(key=lambda h: h["old_start"], reverse=True)
        
        for hunk in hunks:
            start_idx = hunk["old_start"]
            old_len = hunk["old_len"]
            
            found_idx = -1
            for offset in [0] + [x for i in range(1, 31) for x in (i, -i)]:
                candidate_idx = start_idx + offset
                if candidate_idx < 0 or candidate_idx + old_len > len(lines):
                    continue
                    
                match = True
                expected_old_idx = 0
                for dl in hunk["lines"]:
                    if dl.startswith("-") or dl.startswith(" "):
                        expected_line = dl[1:]
                        if candidate_idx + expected_old_idx >= len(lines):
                            match = False
                            break
                        file_line = lines[candidate_idx + expected_old_idx].rstrip("\r\n")
                        if file_line != expected_line.rstrip("\r\n"):
                            match = False
                            break
                        expected_old_idx += 1
                if match:
                    found_idx = candidate_idx
                    break
                    
            if found_idx == -1:
                raise ValueError(f"Hunk alignment failed at target line {start_idx + 1}")
                
            new_block_lines = []
            old_block_len = 0
            for dl in hunk["lines"]:
                if dl.startswith("+"):
                    new_block_lines.append(dl[1:] + "\n")
                elif dl.startswith("-"):
                    old_block_len += 1
                elif dl.startswith(" "):
                    new_block_lines.append(lines[found_idx + old_block_len])
                    old_block_len += 1
                    
            lines[found_idx : found_idx + old_block_len] = new_block_lines
            
        return "".join(lines)

    def _apply_block_replace(self, file_content: str, original: str, patched: str) -> str:
        if not original.strip():
            return file_content
        if original in file_content:
            return file_content.replace(original, patched, 1)
            
        norm_file = file_content.replace("\r\n", "\n")
        norm_orig = original.replace("\r\n", "\n")
        norm_patched = patched.replace("\r\n", "\n")
        if norm_orig in norm_file:
            return norm_file.replace(norm_orig, norm_patched, 1)
            
        raise ValueError("Original block pattern mismatch.")
