# QA Navigator CI Playbook

This document is the master instruction set for QA Navigator CI runs.
The CI runner reads this file at the start of every session and follows it.

## Overview

QA Navigator maintains a library of automation scripts in `qa_scripts/`.
Each script tests a specific capability of a specific app (browser or native desktop).
Scripts are Python files that call harness methods: `find_and_click`, `find_and_type`,
`key_combination`, `get_ui_tree`.

On every CI run, QA Navigator performs three phases:

1. **Replay** — Execute all existing scripts. Fast, no API calls for generation.
2. **Explore** — Detect uncovered UI elements by comparing the live UI tree against
   what existing scripts touch. Generate new scripts for anything missing.
3. **Report** — Produce an HTML report, update the script library, exit with CI code.

## Phase 1: Replay Existing Scripts

For each `.py` file in `qa_scripts/{app_slug}/`:

1. Load the script content as a "plan hint"
2. Feed it to the Gemini function-calling loop alongside a live screenshot
3. The agent follows the script's steps but can adapt if element names changed
4. After execution, vision-validate the result (before/after screenshot comparison)
5. Record: PASS, FAIL, or BROKEN (script references elements that no longer exist)

If a script is BROKEN (>50% of its steps fail to find elements), mark it for
regeneration in Phase 2.

## Phase 2: Explore and Generate

After replaying all existing scripts:

1. Take a fresh screenshot and capture the full UIA tree (or DOM snapshot for browser)
2. Build a "coverage map" — which UI elements are already covered by existing scripts
3. Identify uncovered elements: buttons, menus, inputs, tree items not referenced
   by any script
4. For each uncovered area, generate a new script:
   a. Feed the screenshot + UIA tree + uncovered elements to Gemini Flash
   b. Ask it to design a test script as a JSON step list
   c. Convert to Python via ScriptManager.save()
   d. Run the new script immediately through the function-calling loop
   e. If it passes, keep it. If it fails, discard it.
5. BROKEN scripts from Phase 1 get regenerated here using the same flow

## Phase 3: Report and Commit

1. Generate HTML report with all results (replayed + new)
2. If new scripts were generated or broken ones regenerated:
   - Stage the new/updated files in `qa_scripts/`
   - Commit with message: "qa-navigator: update test scripts [ci skip]"
   - Push to the current branch (in PR context, this updates the PR)
3. Exit with code:
   - 0: all scripts passed
   - 1: one or more scripts failed
   - 2: errors (couldn't reach app, API failures, etc.)

## Script Library Layout

```
qa_scripts/
├── PLAYBOOK.md              # This file
├── {app_slug}/
│   ├── {capability}.py      # One script per testable capability
│   ├── {capability}.py
│   └── ...
└── {another_app}/
    └── ...
```

## Coverage Detection

The coverage map works by parsing existing scripts for element names:
- Extract all `find_and_click("X")` and `find_and_type("Y", ...)` calls
- These are the "covered" elements
- Compare against the live UIA tree's element names
- Elements present in the tree but not in any script = uncovered

This is intentionally simple. It doesn't try to understand semantics —
it just ensures every interactive element has at least one script that touches it.

## Script Regeneration

When a script is marked BROKEN:
1. Keep the old script's description and expected outcome
2. Re-explore the current UI
3. Generate a new script targeting the same capability
4. If the new script passes, overwrite the old one
5. If it also fails, keep the old one and report BROKEN

## Environment Variables

The CI runner respects all standard QA_NAV_* env vars plus:
- `QA_NAV_SCRIPT_DIR` — Path to script library (default: `qa_scripts/`)
- `QA_NAV_CI_MODE` — Set to "true" to enable CI behavior (auto-commit new scripts)
- `QA_NAV_COVERAGE_THRESHOLD` — Minimum % of UI elements covered (default: 80)

## Adding a New App

To add a new application to the test suite:

1. Create `qa_scripts/{app_slug}/` directory
2. Run QA Navigator once with `--ci` mode — it will explore the full UI and
   generate initial scripts
3. Commit the generated scripts
4. Subsequent CI runs will replay them and detect new elements

## Handling UI Changes from PRs

When a PR changes the UI:
- Existing scripts that reference renamed/removed elements will FAIL or go BROKEN
- Phase 2 detects new elements not covered by any script
- New scripts are auto-generated and committed to the PR
- The developer reviews the new scripts alongside their code changes
- Over time, the script library converges to full coverage
