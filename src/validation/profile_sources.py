#!/usr/bin/env python3
"""Profile immutable DfT CSVs using Python's standard library.

Candidate keys and relationships are diagnostic checks, not final business rules.
No source records are rewritten, deduplicated, recoded, or loaded into a database.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CANDIDATE_KEYS = {
    "collisions": ("collision_index",),
    "vehicles": ("collision_index", "vehicle_reference"),
    "casualties": ("collision_index", "casualty_reference"),
}
EXAMPLE_LIMIT = 5


class ProfileInputError(Exception):
    """An input cannot be read reliably enough to profile it."""


def is_blank(value):
    return value is None or value.strip() == ""


def file_metadata(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size_bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def add_example(examples, record_number, row, fields):
    if len(examples) < EXAMPLE_LIMIT:
        examples.append({
            "data_record_number": record_number,
            "identifiers": {field: row.get(field) for field in fields},
        })


def profile_csv(entity, path, parent_indexes):
    """Read one CSV; retain parent-key lookups, not full source rows."""
    key_fields = CANDIDATE_KEYS[entity]
    required = list(dict.fromkeys((*key_fields, "collision_year")))
    if entity == "casualties":
        required.append("vehicle_reference")

    before = path.stat()
    measured = file_metadata(path)
    seen_keys = Counter()
    key_years = {}
    year_counts = Counter()
    relations = Counter()
    examples = {}
    rows = missing_required_rows = malformed_rows = padded_rows = 0

    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=",", quotechar='"', strict=True)
        columns = reader.fieldnames
        if not columns:
            raise ProfileInputError(f"{path.name}: missing header.")
        if any(not column.strip() for column in columns):
            raise ProfileInputError(f"{path.name}: blank column name.")
        if len(columns) != len(set(columns)):
            raise ProfileInputError(f"{path.name}: duplicate column names.")
        missing_columns = sorted(set(required) - set(columns))
        if missing_columns:
            raise ProfileInputError(
                f"{path.name}: required columns missing: {', '.join(missing_columns)}"
            )
        blank_counts = Counter({column: 0 for column in columns})

        for rows, row in enumerate(reader, start=1):
            if None in row or any(row.get(column) is None for column in columns):
                malformed_rows += 1
                add_example(examples.setdefault("malformed_records", []),
                            rows, row, required)
            for column in columns:
                if is_blank(row.get(column)):
                    blank_counts[column] += 1
            if any(is_blank(row.get(column)) for column in required):
                missing_required_rows += 1
                add_example(examples.setdefault("missing_required_values", []),
                            rows, row, required)
            if any(value is not None and value != value.strip()
                   for value in (row.get(column) for column in required)):
                padded_rows += 1

            year = row.get("collision_year")
            year_counts[year if not is_blank(year) else "<blank>"] += 1
            key = tuple(row.get(column) for column in key_fields)
            if all(not is_blank(value) for value in key):
                seen_keys[key] += 1
                if entity in ("collisions", "vehicles"):
                    key_years.setdefault(key, set()).add(year)

            collision_id = row.get("collision_index")
            if entity != "collisions" and not is_blank(collision_id):
                collision_key = (collision_id,)
                parents = parent_indexes["collisions"]
                if collision_key not in parents:
                    relations["orphan_collision_rows"] += 1
                    add_example(examples.setdefault("orphan_collision_rows", []),
                                rows, row, required)
                elif not is_blank(year) and year not in parents[collision_key]:
                    relations["collision_year_mismatch_rows"] += 1
                    add_example(examples.setdefault("collision_year_mismatch_rows", []),
                                rows, row, required)

            if entity == "casualties":
                vehicle_ref = row.get("vehicle_reference")
                if not is_blank(collision_id) and not is_blank(vehicle_ref):
                    vehicle_key = (collision_id, vehicle_ref)
                    parents = parent_indexes["vehicles"]
                    if vehicle_key not in parents:
                        relations["orphan_vehicle_rows"] += 1
                        add_example(examples.setdefault("orphan_vehicle_rows", []),
                                    rows, row, required)
                    elif not is_blank(year) and year not in parents[vehicle_key]:
                        relations["vehicle_year_mismatch_rows"] += 1
                        add_example(examples.setdefault("vehicle_year_mismatch_rows", []),
                                    rows, row, required)

    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ProfileInputError(f"{path.name}: source changed during profiling.")

    duplicate_groups = duplicate_extra_rows = duplicate_rows = 0
    duplicates = []
    for key, count in seen_keys.items():
        if count > 1:
            duplicate_groups += 1
            duplicate_extra_rows += count - 1
            duplicate_rows += count
            if len(duplicates) < EXAMPLE_LIMIT:
                duplicates.append({"key": list(key), "records": count})
    examples["duplicate_candidate_keys"] = duplicates

    profile = {
        "entity": entity,
        "original_filename": path.name,
        **measured,
        "columns": columns,
        "required_columns": required,
        "row_count": rows,
        "rows_by_year": dict(sorted(year_counts.items())),
        "blank_counts_by_column": dict(blank_counts),
        "rows_with_missing_required_values": missing_required_rows,
        "rows_with_padded_required_values": padded_rows,
        "malformed_record_count": malformed_rows,
        "candidate_key": list(key_fields),
        "distinct_complete_candidate_keys": len(seen_keys),
        "duplicate_key_groups": duplicate_groups,
        "duplicate_extra_rows": duplicate_extra_rows,
        "rows_in_duplicate_key_groups": duplicate_rows,
        "relationships": {
            name: relations[name]
            for name in (
                "orphan_collision_rows", "collision_year_mismatch_rows",
                "orphan_vehicle_rows", "vehicle_year_mismatch_rows"
            )
            if name in relations or (
                entity != "collisions"
                and (entity == "casualties" or name.startswith("orphan_collision")
                     or name.startswith("collision_year"))
            )
        },
        "examples": examples,
    }
    return profile, key_years


def make_checks(profile, expected, expected_years):
    """Findings request review; they do not authorise cleaning raw records."""
    checks = []

    def check(name, passed, actual, wanted):
        checks.append({
            "entity": profile["entity"], "check": name,
            "status": "PASS" if passed else "REVIEW",
            "actual": actual, "expected": wanted,
        })

    check("nonempty_file", profile["row_count"] > 0, profile["row_count"], "> 0")
    check("matches_reported_row_count", profile["row_count"] == expected["rows"],
          profile["row_count"], expected["rows"])
    check("matches_reported_year_counts",
          profile["rows_by_year"] == expected["rows_by_year"],
          profile["rows_by_year"], expected["rows_by_year"])
    check("expected_year_coverage",
          set(profile["rows_by_year"]) == set(expected_years),
          sorted(profile["rows_by_year"]), list(expected_years))
    for name, metric in (
        ("required_values_present", "rows_with_missing_required_values"),
        ("candidate_key_unique", "duplicate_extra_rows"),
        ("record_column_counts_match_header", "malformed_record_count"),
        ("required_values_without_surrounding_whitespace",
         "rows_with_padded_required_values"),
    ):
        check(name, profile[metric] == 0, profile[metric], 0)
    for name, count in profile["relationships"].items():
        check(name, count == 0, count, 0)
    return checks


def relative_path(path, root):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise ProfileInputError("Input paths must be inside the project folder.")


def build_report(root, raw_dir, guide_path, baseline, downloaded_at=""):
    """Return findings without writing outputs; useful for verification."""
    batch = raw_dir.name
    if not re.fullmatch(r"[A-Za-z0-9_-]+", batch):
        raise ProfileInputError("The batch folder name must use letters, digits, _ or -.")
    if set(baseline.get("entities", {})) != set(CANDIDATE_KEYS):
        raise ProfileInputError("Baseline must define collisions, vehicles and casualties.")
    years = baseline["expected_years"]
    for entity, expected in baseline["entities"].items():
        if set(expected["rows_by_year"]) != set(years):
            raise ProfileInputError(f"{entity}: baseline years are inconsistent.")
        if sum(expected["rows_by_year"].values()) != expected["rows"]:
            raise ProfileInputError(f"{entity}: baseline yearly counts do not sum to rows.")

    inputs = {}
    for entity in CANDIDATE_KEYS:
        path = raw_dir / baseline["entities"][entity]["filename"]
        relative_path(path, root)
        if not path.is_file():
            raise ProfileInputError(f"Missing {entity} CSV: {relative_path(path, root)}")
        inputs[entity] = path
    relative_path(guide_path, root)
    if not guide_path.is_file():
        raise ProfileInputError(f"Missing guide: {relative_path(guide_path, root)}")

    expected_files = {path.resolve() for path in inputs.values()}
    actual_files = {path.resolve() for path in raw_dir.glob("*.csv")}
    if actual_files != expected_files:
        raise ProfileInputError("The active raw batch must contain exactly the three configured CSVs.")

    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {
        "batch": batch, "inventory_recorded_at_utc": recorded_at,
        "baseline_origin": baseline["baseline_origin"],
        "expected_years": years,
        "definitions": {
            "blank": "Empty, whitespace-only, or a missing CSV cell; coded -1/99 values are not reclassified.",
            "candidate_keys": "Provisional; review against the official guide and discovery findings.",
            "identifiers": "Compared as original strings; no numeric conversion or trimming.",
            "guide": "Inventoried by filename, size and checksum; workbook contents are not interpreted.",
            "data_record_number": "1-based parsed data record after the header, not a physical line number.",
        },
        "profiles": {}, "checks": [], "inventory": [],
    }
    indexes = {}
    for entity, path in inputs.items():
        print(f"Reading {entity}: {path.name}", flush=True)
        profile, index = profile_csv(entity, path, indexes)
        report["profiles"][entity] = profile
        if entity != "casualties":
            indexes[entity] = index
        checks = make_checks(profile, baseline["entities"][entity], years)
        report["checks"].extend(checks)
        review_count = sum(item["status"] == "REVIEW" for item in checks)
        print(f"  {profile['row_count']:,} rows; {review_count} checks need review", flush=True)
        report["inventory"].append({
            "entity": entity, "source_org": "Department for Transport",
            "source_page": baseline["source_page"], "source_download_url": "",
            "original_filename": path.name, "relative_path": relative_path(path, root),
            "downloaded_at_utc": downloaded_at, "inventory_recorded_at_utc": recorded_at,
            "file_format": "csv", "encoding": "utf-8-sig", "delimiter": ",",
            "size_bytes": profile["size_bytes"], "sha256": profile["sha256"],
            "row_count": profile["row_count"], "expected_years": "|".join(years),
            "observed_years": "|".join(profile["rows_by_year"]),
            "status": "REVIEW" if review_count else "PROFILED",
            "notes": "Direct download URL not supplied; no source values changed.",
        })

    report["inventory"].append({
        "entity": "data_guide", "source_org": "Department for Transport",
        "source_page": baseline["source_page"], "source_download_url": "",
        "original_filename": guide_path.name,
        "relative_path": relative_path(guide_path, root),
        "downloaded_at_utc": downloaded_at, "inventory_recorded_at_utc": recorded_at,
        "file_format": guide_path.suffix.lstrip("."), "encoding": "", "delimiter": "",
        **file_metadata(guide_path),
        "row_count": "", "expected_years": "", "observed_years": "",
        "status": "INVENTORIED",
        "notes": "Workbook not parsed; record guide edition and direct URL during discovery.",
    })
    report["status"] = (
        "REVIEW" if any(item["status"] == "REVIEW" for item in report["checks"]) else "PASS"
    )
    return report


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix="profile-", suffix=".tmp", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(text)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def csv_text(rows, fields):
    import io
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def markdown_report(report):
    lines = [
        "# Source profiling: " + report["batch"], "",
        "Result: **" + report["status"] + "**", "",
        "Recorded at UTC: " + report["inventory_recorded_at_utc"], "",
        "Baseline: " + report["baseline_origin"], "",
        "Candidate keys remain provisional. Findings require investigation; raw files were not changed.",
        "",
        "| Entity | Rows | Duplicate extra rows | Rows missing required values |",
        "|---|---:|---:|---:|",
    ]
    for entity, profile in report["profiles"].items():
        lines.append(
            f"| {entity} | {profile['row_count']:,} | "
            f"{profile['duplicate_extra_rows']:,} | "
            f"{profile['rows_with_missing_required_values']:,} |"
        )
    lines += ["", "## Year counts", "", "| Entity | Year | Rows |", "|---|---|---:|"]
    for entity, profile in report["profiles"].items():
        for year, count in profile["rows_by_year"].items():
            lines.append(f"| {entity} | {year} | {count:,} |")
    lines += ["", "## Checks", "", "| Entity | Check | Status |", "|---|---|---|"]
    for check in report["checks"]:
        lines.append(f"| {check['entity']} | {check['check']} | {check['status']} |")
    lines += [
        "", "## Interpretation", "",
        "- PASS applies only to the checks in this report, not to overall source completeness or final modelling.",
        "- REVIEW does not authorise dropping records, changing IDs, or extending keys to hide duplicates.",
        "- Blank counts exclude coded missing-value conventions until those are verified from the guide.",
        "- The JSON report contains actual/expected values and up to five identifier examples for each finding.",
        "- Download timestamps and direct file URLs are left blank unless supplied; filesystem times are not substituted.",
        "- Inspect the workbook's meanings, grains, relationships and specification changes next.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(root, report):
    batch = report["batch"]
    metadata = root / "data" / "metadata"
    discovery = root / "docs" / "01-discovery"
    paths = [
        metadata / f"source_inventory_{batch}.csv",
        metadata / f"schema_inventory_{batch}.csv",
        discovery / f"profiling_{batch}.json",
        discovery / f"profiling_{batch}.md",
    ]
    inventory = report["inventory"]
    atomic_text(paths[0], csv_text(inventory, list(inventory[0])))
    schema_rows = []
    for entity, profile in report["profiles"].items():
        for position, column in enumerate(profile["columns"], start=1):
            schema_rows.append({
                "entity": entity, "original_filename": profile["original_filename"],
                "column_position": position, "column_name": column,
                "profile_read_type": "text", "blank_cells": profile["blank_counts_by_column"][column],
                "in_candidate_key": column in profile["candidate_key"],
            })
    atomic_text(paths[1], csv_text(schema_rows, list(schema_rows[0])))
    atomic_text(paths[2], json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    atomic_text(paths[3], markdown_report(report))
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="data/raw/batch_002")
    parser.add_argument("--guide", default=(
        "data/reference/originals/batch_001/"
        "dft-road-casualty-statistics-road-safety-open-dataset-data-guide-2025.xlsx"
    ))
    parser.add_argument("--baseline", default="data/metadata/baseline_counts_batch_002.json")
    parser.add_argument("--downloaded-at", default="",
                        help="Known UTC download time as ISO 8601; omit if unknown.")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]

    try:
        if args.downloaded_at:
            timestamp = datetime.fromisoformat(args.downloaded_at.replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
                raise ProfileInputError("--downloaded-at must include a UTC timezone.")
        baseline_path = (root / args.baseline).resolve()
        relative_path(baseline_path, root)
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        report = build_report(
            root, (root / args.raw_dir).resolve(), (root / args.guide).resolve(),
            baseline, args.downloaded_at,
        )
        for path in write_outputs(root, report):
            print("Wrote " + relative_path(path, root))
        for check in report["checks"]:
            if check["status"] == "REVIEW":
                print(f"REVIEW {check['entity']}.{check['check']}: {check['actual']}")
        print("PROFILE RESULT: " + report["status"])
        return 1 if report["status"] == "REVIEW" else 0
    except (ProfileInputError, OSError, UnicodeError, csv.Error, ValueError,
            KeyError, TypeError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
