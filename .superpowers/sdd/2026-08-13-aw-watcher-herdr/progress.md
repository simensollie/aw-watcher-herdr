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
Task 2: implemented (commits baddd29..ef83aa4, 29 passed in 1.49s)
Task 2: complete (rounds: 0, spec PASS, quality APPROVED, suite 29 passed in 1.59s)
Task 2: minor (deferred): aw_watcher_herdr/herdr.py:9,130 Two em dashes appear in module comments ("opens a fresh connection — which conveniently" and "ConnectionRefusedError and timeouts —"), violating the plan's global constraint 'No em dashes in prose.' The implementer transcribed the brief's code byte-for-byte (verified via diff against the brief), so this originates in the plan's own code block, not from the implementer's own writing.
Task 2: minor (deferred): aw_watcher_herdr/herdr.py:36 `logger = logging.getLogger(__name__)` is declared at module level but never used anywhere in the file (no logger.debug/info/warning/error call exists). Dead code, present verbatim in the brief's code block, so it is plan-mandated rather than an implementer addition.
Task 3: implemented (commits cecea6a..546f41a, 18/18 passing, full suite 47 passed)
Task 3: complete (rounds: 0, spec PASS, quality APPROVED, suite 47 passed in 1.58s)
Task 4: implemented (commits 042e757..c71b2d9, 17/17 passing, full suite 64 passed)
Task 4: complete (rounds: 0, spec PASS, quality APPROVED, suite 64 passed in 1.68s)
Preflight remediation: rulings 1 (herdr.py docstring), 2 (window_app commented out in DEFAULT_CONFIG + covering test), 7 (DEFAULT_HERDR_BINARY owned by herdr.py, DEFAULT_FLEET_STATUSES owned by state.py, __main__ imports both), 9 (plan table row corrected to 2 agents, fixture test now asserts pane_id w2:p1) applied to tasks 1-4, plus the three deferred minors (__init__ future import, herdr.py em dashes in code and plan, dead logger removed). Suite 65 passed in 2.53s. Evidence: remediation-report.md
Task 5: implemented (commits 8aaa784..5971686, 8/8 test_emit.py passing, 73/73 full suite)
Task 5: complete (rounds: 0, spec PASS, quality APPROVED, suite 73 passed in 2.63s)
Task 6: implemented (commits e1c9d6f..7e5c7f0, 86/86 full suite passing, output pristine)
Task 6: complete (rounds: 0, spec PASS, quality APPROVED, suite 86 passed in 1.76s)
Task 6: minor (deferred): aw_watcher_herdr/lock.py:6, tests/test_lock.py:3 Em dashes appear in new docstring prose ('duplication — so it is prevented here instead.', 'launchd and aw-qt — and two copies'), violating the org-wide 'no em dashes in prose' rule. This is not an isolated slip: the plan's own preflight-remediation log (.superpowers/sdd/2026-08-13-aw-watcher-herdr/progress.md) shows em dashes were already treated as a defect worth fixing in herdr.py during the Task 1-4 remediation round, and Task 6's self-review applied that exact precedent to remove lock.py's dead logger, but did not apply the same precedent to lock.py's and test_lock.py's em dashes, which were transcribed verbatim from the brief without the same cleanup.
Task 7: implemented (commits 0c47d60..3777674, 99/99 passing, output pristine)
Task 7: fix round 1 (run_detect_terminal now uses f"{CLIENT_NAME}-detect" so it does not collide with the running daemon's aw-client single-instance lock, reversing ruling 11 for this call site; commits 3777674..a12830c)
Task 7: complete (rounds: 1, spec PASS, quality CHANGES_REQUESTED, suite 99 passed in 1.84s)
Task 7: minor (deferred): aw_watcher_herdr/query.py:41 `render_attention_query`'s `window_apps` parameter has no type annotation, though the task's interface section specifies `render_attention_query(window_apps: list[str], window_title: str | None = None) -> str`. Without it, and without any defensive normalization, a caller passing a bare string (e.g. `render_attention_query("Ghostty")`) would silently get `list("Ghostty") == ['G','h','o','s','t','t','y']` and render a nonsensical filter -- not currently reachable since the only caller passes `config.window_app`, which `load_config` guarantees is a list, but the gap exists. This is a faithful transcription of the brief's own Step 3 code block, which likewise omits the annotation despite the interface list requiring it.
