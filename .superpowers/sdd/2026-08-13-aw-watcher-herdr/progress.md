# SDD ledger — plan: docs/superpowers/plans/2026-08-13-aw-watcher-herdr.md

Branch: feat/implement-watcher (created from main @ fd7ceb4)
Execution: Workflow tool, sequential per-task pipeline (implement -> verify -> fix loop, max 3 rounds).
Out of scope by controller decision: Task 8 Step 9 (real LaunchAgent install) — the human runs it.

Task 1: implemented (commits fd7ceb4..78e48f3, 9/9 passing, output pristine)
Task 1: fix round 1 (documented default_window_apps() OS-branch as an intentional, narrow exception to the platform rule, commits 78e48f3..2c66673)

Preflight: 11 non-blocking plan defects found (full text: preflight-scan.json).
Controller ruling: fix all 11. #1 docstrings only (code stands), #2 DEFAULT_CONFIG window_app
commented out, #3 lock path gets a -testing suffix, #4 vacuous gap test strengthened,
#5 dead poll-outcome constants deleted, #6 install.sh substitutes the plist template,
#7 one owner per shared default, #8 lock file no longer truncated before flock,
#9 file-structure table corrected + pane identity asserted, #10 dead symbols removed,
#11 diagnostic client uses CLIENT_NAME. Each is a deviation from plan text, recorded here.
Task 1: fix round 2 (amended the platform rule at its sources - spec 4.3/7.1, plan global constraints, herdr.py/lock.py docstrings - to enumerate default_window_apps as a permitted sys.platform site; code docstring now cites the rule, commits 2c66673..505d328)
Task 1: complete (rounds: 2, spec PASS, quality APPROVED, suite 9 passed in 0.02s)
Task 1: minor (deferred): aw_watcher_herdr/__init__.py:1 The binding constraint requires `from __future__ import annotations` in every module, but __init__.py (transcribed verbatim from the brief's Step 2) omits it. Harmless today since the file has no annotations, but it breaks the stated invariant.
