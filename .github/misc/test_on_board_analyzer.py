#!/usr/bin/env python3
"""Contract tests for the on-board selection resolver.

The gold standard is the fixture: these tests assert that the shipped file, the mapping
table and metadata.json's vocabulary cannot drift apart.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github" / "misc"))

import on_board_analyzer as analyzer  # noqa: E402

GOLD = ROOT / ".github" / "misc" / "supportable-board-matrix.yml"
RISCV = "linux-riscv64"


def _run(tmp: Path, metadata: Path, *extra: str) -> tuple[int, Path, Path]:
    out = tmp / "tmp-build-matrix.yml"
    report = tmp / "report.json"
    argv = [
        "--metadata",
        str(metadata),
        "--supportable",
        str(GOLD),
        "--out",
        str(out),
        "--report",
        str(report),
        *extra,
    ]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = analyzer.main(argv)
    return code, out, report


def _metadata(tmp: Path, validation=None, on_board: bool = True) -> Path:
    data = {"name": "demo", "version": "1.2.3", "workflow_triggers": {"on_board": on_board}}
    if validation is not None:
        data["on_board_validation"] = validation
    path = tmp / "metadata.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _selection(os_values, arch_values, versions):
    return {"os": os_values, "profile_arch": arch_values, "toolchain_version": versions}


class GoldStandardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.by_id = analyzer.load_gold(GOLD)

    def test_twelve_verified_targets_without_riscv(self):
        ids = [target["id"] for target in self.data["targets"]]
        self.assertEqual(len(ids), 12)
        self.assertNotIn(RISCV, ids)
        self.assertEqual(len(set(ids)), 12)

    def test_every_target_has_a_citation(self):
        cited = {target_id for record in self.data["verified"] for target_id in record["targets"]}
        ids = {target["id"] for target in self.data["targets"]}
        self.assertEqual(cited, ids)

    def test_citations_reference_known_targets_only(self):
        ids = {target["id"] for target in self.data["targets"]}
        for record in self.data["verified"]:
            self.assertTrue(set(record["targets"]) <= ids, record["run"])

    def test_benchmark_profile_arch_is_not_a_cpu_core(self):
        # On Linux the matrix's profile_arch is Conan's arch family (armv8), never a core;
        # on baremetal the platform deliberately uses the core name for both.
        for target in self.data["targets"]:
            if target["build_kind"] == "linux":
                self.assertFalse(target["benchmark"]["profile_arch"].startswith("cortex-"), target["id"])

    def test_mapping_and_gold_agree(self):
        mapped = {analyzer.TARGET_MAP[key]["id"] for key in analyzer.TARGET_MAP}
        siblings = {
            sibling
            for key in analyzer.TARGET_MAP
            for sibling in analyzer.TARGET_MAP[key].get("siblings", [])
        }
        self.assertTrue(mapped <= set(self.by_id), mapped - set(self.by_id))
        # Every gold target is either reachable from the table or declared as a sibling of one.
        self.assertEqual(set(self.by_id) - mapped, siblings)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_t1_single_target(self):
        code, out, report = _run(self.tmp, _metadata(self.tmp, _selection(["Linux"], ["x86_64"], ["11.3.rel1"])))
        self.assertEqual(code, 0)
        matrix = yaml.safe_load(out.read_text(encoding="utf-8"))
        self.assertEqual(matrix["package_ref"], "demo/1.2.3")
        self.assertEqual([target["id"] for target in matrix["targets"]], ["linux-x86_64"])
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["selection"], _selection(["Linux"], ["x86_64"], ["11.3.rel1"]))
        self.assertIsNone(payload["ids"])

    def test_t2_two_m_cores(self):
        code, out, _ = _run(
            self.tmp, _metadata(self.tmp, _selection(["baremetal"], ["cortex-m33", "cortex-m7"], ["11.3.rel1"]))
        )
        self.assertEqual(code, 0)
        matrix = yaml.safe_load(out.read_text(encoding="utf-8"))
        self.assertEqual(
            [target["id"] for target in matrix["targets"]],
            ["baremetal-cortex-m7", "baremetal-cortex-m33"],
        )

    def test_t3_cross_product_reports_rare_combinations(self):
        code, out, report = _run(
            self.tmp,
            _metadata(self.tmp, _selection(["baremetal", "Linux"], ["cortex-m4", "cortex-a53"], ["11.3.rel1"])),
        )
        self.assertEqual(code, 0)
        matrix = yaml.safe_load(out.read_text(encoding="utf-8"))
        self.assertEqual(
            [target["id"] for target in matrix["targets"]],
            ["linux-orangepi-h6-A53", "baremetal-cortex-m4"],
        )
        warnings = json.loads(report.read_text(encoding="utf-8"))["warnings"]
        self.assertTrue(any("baremetal/cortex-a53" in item for item in warnings))
        self.assertTrue(any("Linux/cortex-m4" in item for item in warnings))

    def test_t4_ambiguous_core_prefers_rk3588(self):
        code, out, report = _run(self.tmp, _metadata(self.tmp, _selection(["Linux"], ["cortex-a76"], ["11.3.rel1"])))
        self.assertEqual(code, 0)
        matrix = yaml.safe_load(out.read_text(encoding="utf-8"))
        self.assertEqual([target["id"] for target in matrix["targets"]], ["linux-orangepi-rk3588-A76"])
        warnings = json.loads(report.read_text(encoding="utf-8"))["warnings"]
        self.assertTrue(any("linux-raspberry-bcm2712-A76" in item for item in warnings))

    def test_t5_unverified_version_fails(self):
        code, out, _ = _run(self.tmp, _metadata(self.tmp, _selection(["Linux"], ["cortex-a53"], ["14.3.rel1"])))
        self.assertEqual(code, 1)
        self.assertFalse(out.exists())

    def test_t6_partial_verification_keeps_the_verified_subset(self):
        code, out, report = _run(
            self.tmp, _metadata(self.tmp, _selection(["Linux"], ["cortex-a53"], ["11.3.rel1", "14.3.rel1"]))
        )
        self.assertEqual(code, 0)
        matrix = yaml.safe_load(out.read_text(encoding="utf-8"))
        self.assertEqual(matrix["targets"][0]["toolchain_versions"], ["11.3.rel1"])
        warnings = json.loads(report.read_text(encoding="utf-8"))["warnings"]
        self.assertTrue(any("14.3.rel1" in item for item in warnings))

    def test_t7_malformed_selection_is_usage_error(self):
        code, _, _ = _run(self.tmp, _metadata(self.tmp, {"os": []}))
        self.assertEqual(code, 2)

    def test_t8_unknown_core_fails(self):
        code, _, _ = _run(self.tmp, _metadata(self.tmp, _selection(["Linux"], ["cortex-a99"], ["11.3.rel1"])))
        self.assertEqual(code, 1)

    def test_t8b_conan_arch_family_is_rejected(self):
        code, _, _ = _run(self.tmp, _metadata(self.tmp, _selection(["Linux"], ["armv8"], ["11.3.rel1"])))
        self.assertEqual(code, 2)

    def test_t9_switch_off_writes_nothing(self):
        code, out, _ = _run(self.tmp, _metadata(self.tmp, _selection(["Linux"], ["x86_64"], ["11.3.rel1"]), on_board=False))
        self.assertEqual(code, 0)
        self.assertFalse(out.exists())

    def test_t10_output_is_byte_identical(self):
        metadata = _metadata(self.tmp, _selection(["baremetal"], ["cortex-m4"], ["11.3.rel1"]))
        first = self.tmp / "first.yml"
        second = self.tmp / "second.yml"
        for path in (first, second):
            code = analyzer.main(
                ["--metadata", str(metadata), "--supportable", str(GOLD), "--out", str(path)]
            )
            self.assertEqual(code, 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_t14_all_mode_emits_every_target(self):
        code, out, report = _run(self.tmp, _metadata(self.tmp, None), "--mode", "all")
        self.assertEqual(code, 0)
        matrix = yaml.safe_load(out.read_text(encoding="utf-8"))
        self.assertEqual(len(matrix["targets"]), 12)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["ids"]), 12)

    def test_t15_ids_mode_rejects_unknown_ids(self):
        code, _, report = _run(self.tmp, _metadata(self.tmp, None), "--mode", "ids", "--ids", "linux-x86_64,nope")
        self.assertEqual(code, 1)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["ids"], ["linux-x86_64", "nope"])
        self.assertIn("nope", payload["error"])


if __name__ == "__main__":
    unittest.main()
