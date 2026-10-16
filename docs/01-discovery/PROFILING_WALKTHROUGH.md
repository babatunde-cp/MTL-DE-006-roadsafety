These files add the first reusable source-profiling step to [babatunde-cp/MTL-DE-006-roadsafety](https://github.com/babatunde-cp/MTL-DE-006-roadsafety).

The inputs are the exact combined five-year filenames you supplied, in data/raw/batch_002. The baseline JSON records your reported counts on 12 October 2026. It is not a publisher-certified completeness check.

| Entity | Your reported rows | Years |
|---|---:|---|
| Collisions | 513,801 | 2021–2025 |
| Vehicles | 937,265 | 2021–2025 |
| Casualties | 652,821 | 2021–2025 |

1. **Open your local working repository in VS Code.**

   Choose File → Open Folder and open the whole cloned MTL-DE-006-roadsafety folder. Open Terminal → New Terminal and run:

   ```bash
   git status
   ```

   If your initial discovery work is still on chore/source-discovery, continue on that branch. Keep any uncommitted work.

   If that branch has already been merged and your working tree is clean, start the next task with:

   ```bash
   git switch main
   git pull --ff-only origin main
   git switch -c feature/source-profiling
   ```

   If switching reports an error or existing changes, preserve them and share the output. Do not use Discard to resolve that situation.

2. **Unzip the download and copy its files into the matching project folders.**

   Double-click the ZIP in Finder. It contains four files:

   | File inside the archive | Destination in your local repository |
   |---|---|
   | src/validation/profile_sources.py | src/validation/profile_sources.py |
   | data/metadata/baseline_counts_batch_002.json | data/metadata/baseline_counts_batch_002.json |
   | tests/test_profile_sources.py | tests/test_profile_sources.py |
   | docs/01-discovery/PROFILING_WALKTHROUGH.md | docs/01-discovery/PROFILING_WALKTHROUGH.md |

   In VS Code, expand src → validation. Right-click validation → Reveal in Finder. Drag profile_sources.py from the extracted archive's src/validation folder into that destination.

   Repeat for the baseline JSON, test file and walkthrough, using their matching destination folders. Preserve your existing README files and other work.

3. **Confirm the original downloads are present and ignored by Git.**

   The script expects:

   ```text
   data/raw/batch_002/dft-road-casualty-statistics-collision-last-5-years.csv
   data/raw/batch_002/dft-road-casualty-statistics-vehicle-last-5-years.csv
   data/raw/batch_002/dft-road-casualty-statistics-casualty-last-5-years.csv
   data/reference/originals/batch_001/dft-road-casualty-statistics-road-safety-open-dataset-data-guide-2025.xlsx
   ```

   Your annual 2025 CSVs remain in batch_001. The profiler selects the active batch explicitly and does not mix those annual files with the five-year files.

   Confirm .gitignore contains these rules, keeping its other rules:

   ```gitignore
   /data/raw/*
   !/data/raw/README.md
   /data/reference/originals/
   ```

   Run:

   ```bash
   git check-ignore -v data/raw/batch_002
   git check-ignore -v data/reference/originals/batch_001
   ```

   Both commands should identify matching ignore rules.

4. **Run the profiler.**

   From the repository root, run:

   ```bash
   python3 src/validation/profile_sources.py
   ```

   No additional packages are needed. The program uses Python's standard library.

   It reads the files and keeps candidate-key lookups in memory. It does not hold every full CSV row at once. Memory use grows with the number of distinct identifiers.

   Its default raw directory, guide path and baseline match the files you supplied. Run the following to see optional path arguments:

   ```bash
   python3 src/validation/profile_sources.py --help
   ```

   It inventories the guide's bytes and checksum; it does not interpret the Excel workbook yet.

5. **Understand the checks.**

   | Check | What it establishes |
   |---|---|
   | Counts and year coverage | Whether the current files agree with your reported baseline and contain 2021–2025 |
   | Required headers | Whether the key and year columns needed by the profiler exist |
   | Blank required values | Whether necessary key/year/reference cells are empty |
   | Candidate key uniqueness | Whether the proposed identifier distinguishes records |
   | Vehicle/casualty to collision | Whether each populated collision reference has a parent record |
   | Casualty to vehicle | Whether its populated collision-and-vehicle reference has a parent vehicle |
   | Parent/child years | Whether linked records have matching years |
   | CSV structure | Whether parsed record widths match the header |
   | Required-field whitespace | Whether surrounding whitespace may affect key comparisons |
   | SHA-256 and byte size | Which exact source-file versions were profiled |

   Candidate keys being tested are:

   | Entity | Candidate key |
   |---|---|
   | Collisions | collision_index |
   | Vehicles | collision_index + vehicle_reference |
   | Casualties | collision_index + casualty_reference |

   These definitions remain provisional until reviewed against the official guide and source findings. A failed candidate-key check calls for investigation.

   IDs stay as their original strings. The profiler does not trim them or convert them to numbers, so leading zeros are preserved.

   Blank counts mean empty/whitespace-only cells or missing CSV cells. Values such as -1 or 99 are retained as source codes; their meanings need the guide. Counts of blanks across optional columns are measurements, not automatic failures.

6. **Open the generated evidence.**

   | Generated file | Purpose |
   |---|---|
   | data/metadata/source_inventory_batch_002.csv | Four source-file entries with names, relative paths, byte sizes, SHA-256 hashes and measured CSV row counts |
   | data/metadata/schema_inventory_batch_002.csv | Actual header names, positions, blank counts and candidate-key membership |
   | docs/01-discovery/profiling_batch_002.json | Detailed check results and up to five identifier examples for each finding |
   | docs/01-discovery/profiling_batch_002.md | A readable summary for review |

   Select the Markdown report in VS Code and press Command + Shift + V to preview it.

   Download timestamps and direct file-download URLs are left blank when unknown. The inventory-recorded timestamp is separate. The program does not invent an acquisition time from file modification times. A known UTC download time can be supplied with --downloaded-at; otherwise leave it blank and record acquisition details when available.

   Schema inventory's text representation describes how the profiler reads values. It does not choose the final SQL column types.

   Rerunning regenerates these four outputs for this batch. Put manual decisions and explanations in the discovery README rather than editing generated results.

7. **Interpret the result.**

   - PASS means this defined set of initial checks passed.
   - REVIEW means one or more findings need investigation. The terminal lists their entity, check name and actual value.
   - ERROR means an input/path/header/parsing issue stopped the run. The command exits with code 2. Do not use a report from a previous run as evidence for the failed run.

   Exit codes are 0 for PASS and 1 for REVIEW. Reports are still written for REVIEW so the evidence remains visible.

   An orphan is a child record whose populated reference cannot find a parent record. Review the identifier examples and official guide before deciding whether it reflects a source problem, coding convention, or an incorrect proposed rule.

   Keep raw data unchanged. Do not drop records, change their IDs, extend keys to conceal duplicates, or change the baseline merely to obtain PASS.

8. **Verify the code with its fictional test cases.**

   Run:

   ```bash
   python3 -m unittest discover -s tests -p test_profile_sources.py -v
   ```

   Eight tests cover valid composite keys, quoted multiline CSV records, duplicate keys, orphan links, leading-zero references, blank keys, mismatched years, required headers, checksums, unchanged raw bytes, reruns and command exit statuses.

   These are synthetic test cases. They do not establish the quality of your actual DfT CSVs; that comes from your own profiling run.

9. **Record your findings and prepare the commit.**

   Add a link to profiling_batch_002.md in docs/01-discovery/README.md. Record the chosen scope, candidate grains/keys and any checks needing review. Preserve the starter documentation.

   Run:

   ```bash
   git status --short --untracked-files=all
   ```

   The status should include the new code, baseline, test, walkthrough and generated metadata/reports. It should not list raw CSVs, the Excel original, virtual environments or databases.

   Review and stage the intended files individually in VS Code Source Control. Use a commit message such as:

   ```text
   Add source inventory and initial quality profiling
   ```

   Publish the current branch, open a pull request in babatunde-cp/MTL-DE-006-roadsafety with main as the base, and follow the team's review process. If findings remain unresolved, use a draft pull request and explain them.

   The next task is to interpret the official guide's grains, keys, coding and missing-value conventions. Then design the raw DuckDB load and reconcile its row counts to this inventory.

Sources and implementation references:

- [Original discovery requirements](https://github.com/Mettelo/MTL-DE-006-UK-Road-Safety-Data-Platform/blob/main/docs/01-discovery/README.md)
- [Original acceptance criteria](https://github.com/Mettelo/MTL-DE-006-UK-Road-Safety-Data-Platform/blob/main/project/ACCEPTANCE_CRITERIA.md)
- [Official data source and guide](https://www.gov.uk/government/statistical-data-sets/road-safety-open-data)
- [Python CSV documentation](https://docs.python.org/3/library/csv.html)
- [Python SHA-256 documentation](https://docs.python.org/3/library/hashlib.html)
