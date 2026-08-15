"""
Repository Scanner and API Regression Suite
============================================
Covers directory traversal, per-file analysis, aggregation, the graph indexes
that keep whole-repository scans linear, and the HTTP routes behind the UI.
"""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# Inject project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph, SymbolNode
from backend.core.analysis.repository_scanner import RepositoryScanner
from backend.main import app


BUGGY = """#include <cstdint>

uint8_t total = 0;

void IRAM_ATTR sensor_isr() {
    delay(100);
    bool data_ready = 1;
}

int readSensor(int channel) {
    rdi.pin("VDD").frobnicate(1.8);
    return channel;
}
"""

CLEAN = """#include <vector>

int sum(const std::vector<int>& values) {
    int acc = 0;
    for (int v : values) { acc += v; }
    return acc;
}
"""


def _make_repo(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "build").mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / "src" / "driver.cpp").write_text(BUGGY, encoding="utf-8")
    (root / "src" / "clean.cpp").write_text(CLEAN, encoding="utf-8")
    (root / "README.md").write_text("not source", encoding="utf-8")
    (root / "build" / "generated.cpp").write_text(BUGGY, encoding="utf-8")
    (root / ".git" / "hook.cpp").write_text(BUGGY, encoding="utf-8")


# ---------------------------------------------------------------------------
# Graph indexes
# ---------------------------------------------------------------------------


class TestRepositoryKnowledgeGraphIndexes(unittest.TestCase):
    """
    A repository scan shares one graph across every file. Without reverse and
    by-file indexes, per-finding lookups walked the whole accumulated graph,
    making a scan quadratic in the number of files.
    """

    def setUp(self):
        self.graph = RepositoryKnowledgeGraph()
        self.graph.register_symbol(SymbolNode(
            name="isr", symbol_type="function", file_path="a.cpp", line_number=1))
        self.graph.register_symbol(SymbolNode(
            name="helper", symbol_type="function", file_path="a.cpp", line_number=9))
        self.graph.register_symbol(SymbolNode(
            name="other", symbol_type="function", file_path="b.cpp", line_number=1))
        self.graph.add_call("isr", "helper")
        self.graph.add_call("other", "helper")

    def test_reverse_caller_index(self):
        self.assertEqual(self.graph.get_callers("helper"), ["isr", "other"])
        self.assertEqual(self.graph.get_callers("nobody"), [])

    def test_by_file_index(self):
        self.assertEqual(self.graph.symbol_names_in_file("a.cpp"), {"isr", "helper"})
        self.assertEqual(self.graph.symbol_names_in_file("b.cpp"), {"other"})
        self.assertEqual(self.graph.symbol_names_in_file("missing.cpp"), set())

    def test_symbols_in_file_returns_nodes(self):
        nodes = self.graph.symbols_in_file("a.cpp")
        self.assertEqual({n.name for n in nodes}, {"isr", "helper"})

    def test_version_increments_on_mutation(self):
        before = self.graph.version
        self.graph.add_call("isr", "brand_new")
        self.assertGreater(self.graph.version, before)

    def test_version_stable_on_duplicate_edge(self):
        self.graph.add_call("isr", "helper")            # already present
        steady = self.graph.version
        self.graph.add_call("isr", "helper")
        self.assertEqual(self.graph.version, steady)

    def test_rehoming_a_symbol_updates_the_file_index(self):
        self.graph.register_symbol(SymbolNode(
            name="helper", symbol_type="function", file_path="moved.cpp", line_number=3))
        self.assertNotIn("helper", self.graph.symbol_names_in_file("a.cpp"))
        self.assertIn("helper", self.graph.symbol_names_in_file("moved.cpp"))


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class TestRepositoryScanner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_repo(self.root)
        self.report = RepositoryScanner().scan(str(self.root))

    def tearDown(self):
        self._tmp.cleanup()

    def _file(self, rel):
        return next((f for f in self.report.files if f.file_path == rel), None)

    def test_only_source_files_are_scanned(self):
        paths = {f.file_path for f in self.report.files}
        self.assertEqual(paths, {"src/driver.cpp", "src/clean.cpp"})

    def test_build_and_vcs_directories_are_pruned(self):
        paths = {f.file_path for f in self.report.files}
        self.assertNotIn("build/generated.cpp", paths)
        self.assertNotIn(".git/hook.cpp", paths)

    def test_findings_are_aggregated(self):
        self.assertEqual(self.report.files_scanned, 2)
        self.assertGreater(self.report.total_findings, 0)
        self.assertEqual(self.report.files_with_findings, 1)
        self.assertEqual(
            self.report.total_findings,
            sum(len(f.findings) for f in self.report.files),
        )

    def test_severity_and_rule_counts_agree_with_findings(self):
        self.assertEqual(
            sum(self.report.severity_counts.values()), self.report.total_findings
        )
        self.assertEqual(
            sum(self.report.rule_counts.values()), self.report.total_findings
        )

    def test_clean_file_has_no_findings(self):
        clean = self._file("src/clean.cpp")
        self.assertIsNotNone(clean)
        self.assertEqual(clean.findings, [])
        self.assertIsNone(clean.error)

    def test_findings_carry_full_enrichment(self):
        driver = self._file("src/driver.cpp")
        self.assertTrue(driver.findings)
        for f in driver.findings:
            self.assertEqual(f.severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"), True)
            self.assertTrue(f.rule_id)
            self.assertGreater(f.evidence_node_count, 0)
            self.assertTrue(f.explanation_markdown)
            self.assertTrue(f.root_cause)
            self.assertTrue(f.strategies, "remediation should yield strategies")

    def test_files_sorted_by_finding_count(self):
        counts = [len(f.findings) for f in self.report.files]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_finding_ids_are_unique(self):
        ids = [f.finding_id for fr in self.report.files for f in fr.findings]
        self.assertEqual(len(ids), len(set(ids)))

    def test_max_files_truncates(self):
        report = RepositoryScanner(max_files=1).scan(str(self.root))
        self.assertTrue(report.truncated)
        self.assertEqual(report.files_scanned, 1)

    def test_oversized_files_are_skipped(self):
        report = RepositoryScanner(max_file_size_bytes=10).scan(str(self.root))
        self.assertEqual(report.files_scanned, 0)
        self.assertEqual(report.files_skipped, 2)

    def test_unreadable_file_does_not_abort_the_scan(self):
        (self.root / "src" / "binary.cpp").write_bytes(b"\xff\xfe\x00\x01 int x = ;")
        report = RepositoryScanner().scan(str(self.root))
        self.assertEqual(report.files_scanned, 3)
        self.assertEqual(report.files_errored, 0)

    def test_missing_root_raises(self):
        with self.assertRaises(NotADirectoryError):
            RepositoryScanner().scan(str(self.root / "nope"))


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestRepositoryRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        _make_repo(cls.root)

        res = cls.client.post("/repository/scan", json={"path": str(cls.root)})
        assert res.status_code == 200, res.text
        cls.scan = res.json()
        cls.scan_id = cls.scan["scan_id"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_scan_local_path(self):
        self.assertEqual(self.scan["files_scanned"], 2)
        self.assertGreater(self.scan["total_findings"], 0)

    def test_scan_rejects_missing_path(self):
        res = self.client.post("/repository/scan", json={"path": "/definitely/not/here"})
        self.assertEqual(res.status_code, 400)

    def test_scan_rejects_a_file_path(self):
        res = self.client.post(
            "/repository/scan", json={"path": str(self.root / "README.md")}
        )
        self.assertEqual(res.status_code, 400)

    def test_get_scan_by_id(self):
        res = self.client.get(f"/repository/scan/{self.scan_id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["scan_id"], self.scan_id)

    def test_unknown_scan_id_is_404(self):
        self.assertEqual(self.client.get("/repository/scan/nope").status_code, 404)

    def test_list_scans(self):
        res = self.client.get("/repository/scans")
        self.assertEqual(res.status_code, 200)
        self.assertIn(self.scan_id, [s["scan_id"] for s in res.json()])

    def test_fetch_source_file(self):
        res = self.client.get(
            f"/repository/scan/{self.scan_id}/file", params={"path": "src/driver.cpp"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("IRAM_ATTR", res.json()["content"])

    def test_path_traversal_is_rejected(self):
        res = self.client.get(
            f"/repository/scan/{self.scan_id}/file",
            params={"path": "../../../../Windows/System32/drivers/etc/hosts"},
        )
        self.assertIn(res.status_code, (400, 404))

    def test_missing_source_file_is_404(self):
        res = self.client.get(
            f"/repository/scan/{self.scan_id}/file", params={"path": "src/ghost.cpp"}
        )
        self.assertEqual(res.status_code, 404)

    def test_all_export_formats(self):
        for fmt in ("json", "sarif", "markdown", "html"):
            with self.subTest(fmt=fmt):
                res = self.client.get(f"/repository/scan/{self.scan_id}/export/{fmt}")
                self.assertEqual(res.status_code, 200)
                self.assertGreater(len(res.content), 0)
                self.assertIn("attachment", res.headers["content-disposition"])

    def test_sarif_export_is_valid(self):
        res = self.client.get(f"/repository/scan/{self.scan_id}/export/sarif")
        doc = json.loads(res.content)
        self.assertEqual(doc["version"], "2.1.0")
        results = doc["runs"][0]["results"]
        self.assertEqual(len(results), self.scan["total_findings"])
        uri = results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertTrue(uri.startswith("src/"))

    def test_unsupported_export_format(self):
        res = self.client.get(f"/repository/scan/{self.scan_id}/export/pdf")
        self.assertEqual(res.status_code, 400)

    # ── Upload ──────────────────────────────────────────────────────

    @staticmethod
    def _zip_bytes(members: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, content in members.items():
                z.writestr(name, content)
        return buf.getvalue()

    def test_upload_archive(self):
        payload = self._zip_bytes({
            "myrepo/src/driver.cpp": BUGGY,
            "myrepo/src/clean.cpp": CLEAN,
        })
        res = self.client.post(
            "/repository/upload",
            files={"file": ("myrepo.zip", payload, "application/zip")},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["source_label"], "myrepo.zip")
        self.assertEqual(body["files_scanned"], 2)
        # The single top-level folder is collapsed, so paths are not prefixed.
        self.assertIn("src/driver.cpp", {f["file_path"] for f in body["files"]})

    def test_upload_rejects_non_zip(self):
        res = self.client.post(
            "/repository/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(res.status_code, 400)

    def test_upload_rejects_corrupt_zip(self):
        res = self.client.post(
            "/repository/upload",
            files={"file": ("broken.zip", b"not really a zip", "application/zip")},
        )
        self.assertEqual(res.status_code, 400)

    def test_upload_rejects_zip_slip(self):
        payload = self._zip_bytes({"../escaped.cpp": BUGGY})
        res = self.client.post(
            "/repository/upload",
            files={"file": ("evil.zip", payload, "application/zip")},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
