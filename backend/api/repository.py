"""
Repository Analysis API
=======================
Routes backing the browser UI:

    POST /repository/scan          analyse a directory already on this machine
    POST /repository/upload        analyse an uploaded .zip archive
    GET  /repository/scans         list scans held in memory
    GET  /repository/scan/{id}     re-fetch a completed scan
    GET  /repository/scan/{id}/file?path=...   source of one scanned file
    GET  /repository/scan/{id}/export/{fmt}    json | sarif | markdown | html

Scans are held in a bounded in-process cache so the UI can re-open a report
and download exports without re-analysing. This is a local developer tool,
not a multi-tenant service: there is no persistence and no auth.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from collections import OrderedDict
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.core.analysis.repository_scanner import (
    DEFAULT_EXTENSIONS,
    RepositoryReport,
    RepositoryScanner,
)

logger = logging.getLogger("backend.api.repository")

router = APIRouter(prefix="/repository", tags=["repository"])

# Bounded LRU of completed scans: scan_id -> (report, root_dir_or_None)
_MAX_CACHED_SCANS = 10
_scans: "OrderedDict[str, dict]" = OrderedDict()

# Uploads are capped so a stray file cannot exhaust disk or memory.
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024      # 100 MB


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    path: str = Field(description="Absolute path to a repository on this machine.")
    max_files: int = Field(default=2000, ge=1, le=20000)
    extensions: Optional[List[str]] = Field(
        default=None,
        description="Override the analysed file extensions.",
    )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _remember(report: RepositoryReport, temp_root: Optional[str]) -> None:
    """Cache a scan, evicting (and cleaning up) the oldest when full."""
    _scans[report.scan_id] = {"report": report, "temp_root": temp_root}
    _scans.move_to_end(report.scan_id)
    while len(_scans) > _MAX_CACHED_SCANS:
        _, evicted = _scans.popitem(last=False)
        stale = evicted.get("temp_root")
        if stale and os.path.isdir(stale):
            shutil.rmtree(stale, ignore_errors=True)
            logger.info("Evicted cached scan; removed %s", stale)


def _get(scan_id: str) -> dict:
    entry = _scans.get(scan_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown scan_id '{scan_id}'.")
    _scans.move_to_end(scan_id)
    return entry


# ---------------------------------------------------------------------------
# Scan routes
# ---------------------------------------------------------------------------


@router.post("/scan", response_model=RepositoryReport)
def scan_local_path(req: ScanRequest) -> RepositoryReport:
    """Analyse a repository that already exists on this machine."""
    path = os.path.abspath(os.path.expanduser(req.path.strip()))

    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"Path does not exist: {path}")
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    scanner = RepositoryScanner(
        extensions=tuple(req.extensions) if req.extensions else None,
        max_files=req.max_files,
    )
    try:
        report = scanner.scan(path, source_label=path)
    except Exception as exc:
        logger.error("Scan of %s failed: %s", path, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scan failed: {exc}") from exc

    # The directory is the user's own; never delete it on eviction.
    _remember(report, temp_root=None)
    return report


@router.post("/upload", response_model=RepositoryReport)
async def scan_uploaded_archive(
    file: UploadFile = File(..., description="A .zip archive of the repository."),
    max_files: int = Query(default=2000, ge=1, le=20000),
) -> RepositoryReport:
    """Analyse an uploaded .zip archive."""
    filename = file.filename or "upload.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=400,
            detail="Only .zip archives are supported. Zip the repository first.",
        )

    payload = await file.read()
    if len(payload) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Archive exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    extract_root = tempfile.mkdtemp(prefix="argus_upload_")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            _safe_extract(archive, extract_root)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Archive is not a valid zip file.") from exc
    except Exception as exc:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Could not extract archive: {exc}") from exc

    # GitHub-style zips wrap everything in one top-level folder; scan that
    # directly so reported paths are not prefixed with "repo-main/".
    scan_root = _collapse_single_root(extract_root)

    try:
        report = RepositoryScanner(max_files=max_files).scan(
            scan_root, source_label=filename
        )
    except Exception as exc:
        shutil.rmtree(extract_root, ignore_errors=True)
        logger.error("Scan of upload %s failed: %s", filename, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scan failed: {exc}") from exc

    # Keep the extracted tree alive so source can be viewed, and register it
    # for cleanup when this scan is evicted.
    _remember(report, temp_root=extract_root)
    return report


# ---------------------------------------------------------------------------
# Retrieval routes
# ---------------------------------------------------------------------------


@router.get("/scans")
def list_scans() -> List[dict]:
    """Summaries of the scans currently held in memory, newest first."""
    out = []
    for scan_id, entry in reversed(_scans.items()):
        r: RepositoryReport = entry["report"]
        out.append({
            "scan_id": scan_id,
            "source_label": r.source_label,
            "files_scanned": r.files_scanned,
            "total_findings": r.total_findings,
            "scanned_at": r.scanned_at,
        })
    return out


@router.get("/scan/{scan_id}", response_model=RepositoryReport)
def get_scan(scan_id: str) -> RepositoryReport:
    """Re-fetch a completed scan."""
    return _get(scan_id)["report"]


@router.get("/scan/{scan_id}/file")
def get_source_file(scan_id: str, path: str = Query(...)) -> dict:
    """
    Return the source of one file from a scan, for the inline code view.

    The requested path is resolved against the scan root and rejected if it
    escapes it, so a crafted `path` cannot read arbitrary files.
    """
    entry = _get(scan_id)
    report: RepositoryReport = entry["report"]

    root = os.path.abspath(report.root)
    target = os.path.abspath(os.path.join(root, path))
    if os.path.commonpath([root, target]) != root:
        raise HTTPException(status_code=400, detail="Path escapes the scan root.")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail=f"No such file in scan: {path}")

    try:
        with open(target, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read file: {exc}") from exc

    return {"path": path, "content": content, "line_count": content.count("\n") + 1}


# ---------------------------------------------------------------------------
# Export routes
# ---------------------------------------------------------------------------


_EXPORT_MEDIA = {
    "json":     ("application/json",  "json"),
    "sarif":    ("application/json",  "sarif"),
    "markdown": ("text/markdown",     "md"),
    "html":     ("text/html",         "html"),
}


@router.get("/scan/{scan_id}/export/{fmt}")
def export_scan(scan_id: str, fmt: str) -> Response:
    """Download the whole scan as JSON, SARIF 2.1.0, Markdown, or HTML."""
    fmt = fmt.lower()
    if fmt not in _EXPORT_MEDIA:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{fmt}'. Use one of: {', '.join(_EXPORT_MEDIA)}.",
        )

    report: RepositoryReport = _get(scan_id)["report"]
    media_type, extension = _EXPORT_MEDIA[fmt]

    if fmt == "json":
        body = json.dumps(report.model_dump(), indent=2, default=str).encode("utf-8")
    elif fmt == "sarif":
        body = json.dumps(_to_sarif(report), indent=2).encode("utf-8")
    elif fmt == "markdown":
        body = _to_markdown(report).encode("utf-8")
    else:
        body = _to_html(report).encode("utf-8")

    filename = f"argus-scan-{scan_id[:8]}.{extension}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Export renderers
# ---------------------------------------------------------------------------

_SARIF_LEVEL = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning", "LOW": "note"}


def _to_sarif(report: RepositoryReport) -> dict:
    rules: Dict[str, dict] = {}
    results: List[dict] = []

    for fr in report.files:
        for f in fr.findings:
            rules.setdefault(f.rule_id, {
                "id": f.rule_id,
                "name": f.rule_id.replace("_", " ").title(),
                "shortDescription": {"text": f.description},
                "properties": {"severity": f.severity},
            })
            results.append({
                "ruleId": f.rule_id,
                "level": _SARIF_LEVEL.get(f.severity, "note"),
                "message": {"text": f.description},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": fr.file_path},
                        "region": {"startLine": f.line_number or 1},
                    }
                }],
                "properties": {
                    "confidence": f.confidence,
                    "rootCause": f.root_cause,
                },
            })

    return {
        "$schema": "https://schemastore.org/schemas/json/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "ARGUS",
                "version": "3.0.0",
                "informationUri": "https://github.com/Harsha2oo5/Argus",
                "rules": list(rules.values()),
            }},
            "results": results,
        }],
    }


def _to_markdown(report: RepositoryReport) -> str:
    lines = [
        "# ARGUS Repository Analysis Report",
        "",
        f"**Source:** `{report.source_label}`  ",
        f"**Files scanned:** {report.files_scanned}  ",
        f"**Findings:** {report.total_findings}  ",
        f"**Suppressed:** {report.suppressed_count}  ",
        f"**Duration:** {report.duration_ms:.0f} ms",
        "",
        "## Severity Summary",
        "",
        "| Severity | Count |",
        "|---|---|",
    ]
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        if report.severity_counts.get(sev):
            lines.append(f"| {sev} | {report.severity_counts[sev]} |")

    lines += ["", "## Findings by Rule", "", "| Rule | Count |", "|---|---|"]
    for rule, count in sorted(report.rule_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{rule}` | {count} |")

    lines += ["", "---", "", "## Findings", ""]
    for fr in report.files:
        if not fr.findings:
            continue
        lines += [f"### `{fr.file_path}`", ""]
        for f in fr.findings:
            lines += [
                f"#### [{f.severity}] `{f.rule_id}` — line {f.line_number}",
                "",
                f"{f.description}",
                "",
                f"```cpp\n{f.line_text.strip()}\n```",
                "",
                f"- **Confidence:** {f.confidence:.2f}",
                f"- **Evidence:** {f.evidence}",
                f"- **Root cause:** {f.root_cause or 'n/a'}",
                f"- **Remediation:** {f.remediation}",
                "",
            ]
            if f.strategies:
                lines.append("**Repair strategies:**")
                lines.append("")
                for s in f.strategies:
                    mark = "accepted" if s.accepted else "considered"
                    lines.append(
                        f"- `{s.strategy_id}` (score {s.patch_score:.2f}, "
                        f"risk {s.risk:.2f}, {mark}) — {s.description}"
                    )
                lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _to_html(report: RepositoryReport) -> str:
    from html import escape

    rows: List[str] = []
    for fr in report.files:
        for f in fr.findings:
            rows.append(
                f'<tr class="sev-{f.severity.lower()}">'
                f'<td><span class="badge {f.severity.lower()}">{f.severity}</span></td>'
                f'<td><code>{escape(f.rule_id)}</code></td>'
                f'<td><code>{escape(fr.file_path)}</code>:{f.line_number or "?"}</td>'
                f'<td>{escape(f.description)}</td>'
                f'<td>{f.confidence:.2f}</td>'
                f'</tr>'
            )

    pills = " ".join(
        f'<span class="badge {s.lower()}">{s}: {c}</span>'
        for s, c in report.severity_counts.items()
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>ARGUS Repository Report</title>
<style>
 body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: auto;
        padding: 2rem; background: #0d0f14; color: #e2e8f0; }}
 h1 {{ color: #00e5a0; }}
 .badge {{ padding: 2px 8px; border-radius: 4px; font-size: .75rem;
           font-weight: 700; margin-right: 4px; }}
 .critical {{ background:#7f1d1d; color:#fca5a5; }}
 .high     {{ background:#7c2d12; color:#fed7aa; }}
 .medium   {{ background:#713f12; color:#fde68a; }}
 .low      {{ background:#1e3a5f; color:#93c5fd; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
 th, td {{ text-align:left; padding:8px 12px; border-bottom:1px solid #252a38; }}
 th {{ background:#13161d; color:#8892a4; }}
 tr:hover {{ background:#1e233044; }}
 code {{ background:#13161d; padding:1px 4px; border-radius:3px; font-size:.85rem; }}
 .meta {{ color:#8892a4; margin-bottom:1rem; }}
</style></head><body>
<h1>ARGUS Repository Report</h1>
<p class="meta">Source: <code>{escape(report.source_label)}</code><br>
{report.files_scanned} files scanned &middot; {report.total_findings} findings &middot;
{report.suppressed_count} suppressed &middot; {report.duration_ms:.0f} ms</p>
<div>{pills}</div>
<table><thead><tr>
<th>Severity</th><th>Rule</th><th>Location</th><th>Description</th><th>Confidence</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""


# ---------------------------------------------------------------------------
# Archive helpers
# ---------------------------------------------------------------------------


def _safe_extract(archive: zipfile.ZipFile, dest: str) -> None:
    """
    Extract *archive* into *dest*, rejecting entries that escape it.

    Guards against zip-slip (``../`` members and absolute paths) and against
    a zip bomb via a total-uncompressed-size cap.
    """
    dest_root = os.path.abspath(dest)
    total = 0

    for member in archive.infolist():
        if member.is_dir():
            continue
        total += member.file_size
        if total > _MAX_UPLOAD_BYTES * 4:
            raise ValueError("Archive expands to an unreasonable size.")

        target = os.path.abspath(os.path.join(dest_root, member.filename))
        if os.path.commonpath([dest_root, target]) != dest_root:
            raise ValueError(f"Unsafe path in archive: {member.filename}")

        os.makedirs(os.path.dirname(target), exist_ok=True)
        with archive.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _collapse_single_root(path: str) -> str:
    """If *path* contains exactly one directory and nothing else, descend."""
    try:
        entries = os.listdir(path)
    except OSError:
        return path
    if len(entries) == 1:
        only = os.path.join(path, entries[0])
        if os.path.isdir(only):
            return only
    return path
