"""Small fictional datasets exercise key and relationship failures, not real DfT results."""

import contextlib
import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE_ROOT / "src" / "validation"))
from profile_sources import ProfileInputError, build_report, write_outputs

FILENAMES = {
    "collisions": "dft-road-casualty-statistics-collision-last-5-years.csv",
    "vehicles": "dft-road-casualty-statistics-vehicle-last-5-years.csv",
    "casualties": "dft-road-casualty-statistics-casualty-last-5-years.csv",
}
COLUMNS = {
    "collisions": ["collision_index", "collision_year", "note"],
    "vehicles": ["collision_index", "collision_year", "vehicle_reference"],
    "casualties": [
        "collision_index", "collision_year", "vehicle_reference", "casualty_reference"
    ],
}


class SourceProfilingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "data" / "raw" / "batch_002"
        self.raw.mkdir(parents=True)
        self.guide = (
            self.root / "data" / "reference" / "originals" / "batch_001"
            / "dft-road-casualty-statistics-road-safety-open-dataset-data-guide-2025.xlsx"
        )
        self.guide.parent.mkdir(parents=True)
        # Only checksum/inventory behaviour is tested; this is not a real workbook.
        self.guide.write_bytes(b"fictional guide placeholder, not parsed")
        self.rows = {
            "collisions": [
                {"collision_index": "0001", "collision_year": "2021", "note": "quoted, value\nsecond line"},
                {"collision_index": "0002", "collision_year": "2021", "note": "Unicode: café"},
            ],
            "vehicles": [
                {"collision_index": "0001", "collision_year": "2021", "vehicle_reference": "01"},
                {"collision_index": "0001", "collision_year": "2021", "vehicle_reference": "02"},
                {"collision_index": "0002", "collision_year": "2021", "vehicle_reference": "01"},
            ],
            "casualties": [
                {"collision_index": "0001", "collision_year": "2021",
                 "vehicle_reference": "01", "casualty_reference": "001"},
                {"collision_index": "0001", "collision_year": "2021",
                 "vehicle_reference": "02", "casualty_reference": "002"},
                {"collision_index": "0002", "collision_year": "2021",
                 "vehicle_reference": "01", "casualty_reference": "001"},
            ],
        }
        self.baseline = self.make_baseline()

    def make_baseline(self):
        return {
            "baseline_origin": "Fictional test fixtures, not DfT source counts.",
            "source_page": "https://www.gov.uk/government/statistical-data-sets/road-safety-open-data",
            "expected_years": ["2021"],
            "entities": {
                entity: {
                    "filename": FILENAMES[entity], "rows": len(rows),
                    "rows_by_year": dict(Counter(row["collision_year"] for row in rows)),
                }
                for entity, rows in self.rows.items()
            },
        }

    def write_inputs(self):
        for entity, rows in self.rows.items():
            with (self.raw / FILENAMES[entity]).open(
                "w", encoding="utf-8-sig", newline=""
            ) as output:
                writer = csv.DictWriter(output, fieldnames=COLUMNS[entity])
                writer.writeheader()
                writer.writerows(rows)

    def profile(self):
        self.write_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            return build_report(self.root, self.raw, self.guide, self.baseline)

    def test_valid_composite_keys_and_quoted_multiline_records_pass(self):
        report = self.profile()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["profiles"]["collisions"]["row_count"], 2)
        self.assertEqual(report["profiles"]["vehicles"]["duplicate_extra_rows"], 0)
        self.assertEqual(report["profiles"]["casualties"]["duplicate_extra_rows"], 0)
        self.assertEqual(report["profiles"]["casualties"]["relationships"]["orphan_vehicle_rows"], 0)
        self.assertEqual(report["profiles"]["collisions"]["candidate_key"], ["collision_index"])

    def test_duplicate_vehicle_key_is_visible_without_deduplication(self):
        self.rows["vehicles"].append(dict(self.rows["vehicles"][0]))
        self.baseline = self.make_baseline()
        report = self.profile()
        vehicle = report["profiles"]["vehicles"]
        self.assertEqual(report["status"], "REVIEW")
        self.assertEqual(vehicle["row_count"], 4)
        self.assertEqual(vehicle["duplicate_extra_rows"], 1)
        self.assertEqual(vehicle["rows_in_duplicate_key_groups"], 2)
        self.assertEqual(vehicle["examples"]["duplicate_candidate_keys"][0]["key"], ["0001", "01"])

    def test_orphan_collision_and_vehicle_are_both_reported(self):
        self.rows["casualties"][0]["collision_index"] = "9999"
        report = self.profile()
        relationships = report["profiles"]["casualties"]["relationships"]
        self.assertEqual(report["status"], "REVIEW")
        self.assertEqual(relationships["orphan_collision_rows"], 1)
        self.assertEqual(relationships["orphan_vehicle_rows"], 1)

    def test_leading_zero_references_are_not_silently_converted(self):
        self.rows["casualties"][0]["vehicle_reference"] = "1"
        report = self.profile()
        self.assertEqual(report["profiles"]["casualties"]["relationships"]["orphan_vehicle_rows"], 1)

    def test_blank_key_and_parent_year_mismatch_are_reported(self):
        self.rows["casualties"][0]["casualty_reference"] = ""
        self.rows["casualties"][1]["collision_year"] = "2022"
        report = self.profile()
        casualty = report["profiles"]["casualties"]
        self.assertEqual(report["status"], "REVIEW")
        self.assertEqual(casualty["rows_with_missing_required_values"], 1)
        self.assertEqual(casualty["relationships"]["collision_year_mismatch_rows"], 1)
        self.assertEqual(casualty["relationships"]["vehicle_year_mismatch_rows"], 1)

    def test_missing_required_header_stops_the_profile(self):
        self.write_inputs()
        vehicle = self.raw / FILENAMES["vehicles"]
        vehicle.write_text("collision_index,collision_year\n0001,2021\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ProfileInputError, "vehicle_reference"):
                build_report(self.root, self.raw, self.guide, self.baseline)

    def test_outputs_and_reruns_preserve_all_original_bytes(self):
        report = self.profile()
        sources = [*self.raw.glob("*.csv"), self.guide]
        before = {path: path.read_bytes() for path in sources}
        paths = write_outputs(self.root, report)
        self.assertEqual(len(paths), 4)
        with paths[0].open(newline="") as source:
            inventory = list(csv.DictReader(source))
        self.assertEqual(len(inventory), 4)
        for row in inventory:
            path = self.root / row["relative_path"]
            self.assertEqual(row["sha256"], hashlib.sha256(before[path]).hexdigest())
            self.assertEqual(row["downloaded_at_utc"], "")
        with contextlib.redirect_stdout(io.StringIO()):
            rerun = build_report(self.root, self.raw, self.guide, self.baseline)
        write_outputs(self.root, rerun)
        self.assertEqual(rerun["profiles"], report["profiles"])
        self.assertEqual(before, {path: path.read_bytes() for path in sources})

    def test_cli_reports_pass_review_and_input_error_exit_codes(self):
        self.write_inputs()
        script = self.root / "src" / "validation" / "profile_sources.py"
        script.parent.mkdir(parents=True)
        shutil.copyfile(BUNDLE_ROOT / "src" / "validation" / "profile_sources.py", script)
        baseline = self.root / "data" / "metadata" / "baseline_counts_batch_002.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text(json.dumps(self.baseline), encoding="utf-8")

        def run():
            return subprocess.run(
                [sys.executable, str(script)], cwd=self.root,
                capture_output=True, text=True, check=False,
            )

        passed = run()
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("PROFILE RESULT: PASS", passed.stdout)
        self.rows["casualties"][0]["collision_index"] = "9999"
        self.write_inputs()
        reviewed = run()
        self.assertEqual(reviewed.returncode, 1, reviewed.stderr)
        self.assertIn("PROFILE RESULT: REVIEW", reviewed.stdout)
        self.guide.unlink()
        invalid = run()
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("Missing guide", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
