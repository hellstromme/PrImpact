PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS = """\
You are analysing a code change. Your job is to extract design information that a
human reviewer needs in order to evaluate whether the change fits their system and roadmap.

You will be given:
- The diff for each changed file
- The public interface of files in the blast radius (signatures only, not implementations)

Respond in JSON matching this schema exactly:
{{
  "summary": "string — what the system now does differently, in plain English, 2-3 sentences",
  "decisions": [
    {{
      "description": "what approach was chosen",
      "rationale": "why, as inferred from the code",
      "risk": "what breaks if the rationale is wrong"
    }}
  ],
  "assumptions": [
    {{
      "description": "what must be true for this design to be correct",
      "location": "file and function where this is baked in",
      "risk": "consequence if assumption is violated"
    }}
  ]
}}

Changed files (full diff):
{changed_files_diff}

Blast radius interfaces (signatures only):
{blast_radius_signatures}
"""

PROMPT_ANOMALY_DETECTION = """\
You are reviewing a code change for structural anomalies — patterns that deviate from
the conventions visible in the surrounding codebase.

You will be given:
- The diff for each changed file
- The import/declaration signatures of the changed files before this PR — use these
  to understand what patterns were already established inside those files
- Examples of the established patterns in nearby unchanged files (signatures and import structure)

Identify deviations that a reviewer should be aware of. Do not flag style differences.
Flag things that suggest the change may not fit the architecture — unusual coupling,
bypassed abstractions, patterns used in a context where they are not normally used.

IMPORTANT: If the before-signatures of a changed file already show the same pattern
(e.g. the same kind of import or the same calling convention), treat that pattern as
established and do not flag additions that follow it.

Respond in JSON:
{{
  "anomalies": [
    {{
      "description": "what is unusual",
      "location": "file and approximate line",
      "severity": "low | medium | high"
    }}
  ]
}}

Changed files (diff):
{changed_files_diff}

Signatures of changed files before this PR:
{changed_files_before_signatures}

Established patterns in neighbouring unchanged files:
{neighbouring_signatures}
"""

PROMPT_SECURITY_SIGNALS = """\
You are reviewing security signals detected in a pull request diff.

For each pattern signal below, assess whether it is surprising given:
- The file's stated purpose (inferred from its path and the code that existed before the change)
- The surrounding module's existing patterns
- The broader codebase context

Rules for severity:
- Downgrade to "low" if the same pattern already exists in the file before this change
- Downgrade if the file is clearly an infrastructure/build/deploy script where this pattern is expected
- Keep as "high" if the pattern appears in a module that had no prior similar behaviour

Respond in JSON — an array of objects matching this schema exactly:
[
  {{
    "description": "concise description of what was detected",
    "file_path": "path/to/file",
    "line_number": 42,
    "signal_type": "network_call | credential | encoded_payload | dynamic_exec | shell_invoke | suspicious_import",
    "severity": "high | medium | low",
    "why_unusual": "why this is notable in this specific file and module context",
    "suggested_action": "what the reviewer should do or ask about"
  }}
]

If a signal is clearly benign, include it with severity "low" and explain why in why_unusual.
Return an empty array [] if there are no signals.

Changed files (full diff — use this to verify whether the signal is genuinely new code):
{changed_files_diff}

Pattern signals detected:
{pattern_signals}

File context (code that existed before the change, signatures only):
{file_context}
"""

PROMPT_TEST_GAP_ANALYSIS = """\
You are analysing a code change to identify behaviours that are not covered by tests.

You will be given:
- The diff for each changed file
- The test files that exist in the repo for the changed modules (if any)

Identify changed or new behaviours that have no corresponding test. Focus on:
- New code paths (especially error paths and edge cases)
- Changed logic in existing functions
- New exported symbols with no test file

Do not flag missing tests for trivial getters/setters or purely structural changes.

Respond in JSON:
{{
  "test_gaps": [
    {{
      "behaviour": "description of the untested behaviour in plain English",
      "location": "file and function"
    }}
  ]
}}

Changed files:
{changed_files_diff}

Existing test files for these modules:
{test_files}
"""

PROMPT_SEMANTIC_EQUIVALENCE = """\
You are analysing code changes to classify whether each changed symbol is semantically
equivalent to what it replaced, or whether it introduces genuinely new behaviour.

You will be given:
- The diff for each changed file, including before and after signatures

For each changed function or class, assess whether the change is:
- "equivalent": a refactor, rename, reformat, or reorder — the observable behaviour is
  unchanged (e.g. extract method, rename variable, reorder parameters with same defaults,
  add type annotations that don't change runtime behaviour)
- "risky": a small-looking diff that alters branching logic, adds a new code path, changes
  state transitions, or modifies return values in a non-obvious way
- "normal": a genuine, straightforward change that fits neither category above

Only flag "equivalent" when you are confident the behaviour is unchanged.
Only flag "risky" when a small diff has disproportionate semantic impact.
Leave "normal" changes unlabelled — only return entries for "equivalent" and "risky".

Respond in JSON — an array (may be empty):
[
  {{
    "file": "path/to/file.py",
    "symbol": "function_or_class_name",
    "verdict": "equivalent | risky",
    "reason": "one sentence explaining the classification"
  }}
]

Changed files (diff with before/after context):
{changed_files_diff}

Signatures before and after:
{signatures_before_after}
"""

PROMPT_VERDICT = """\
You are a merge-readiness gate for a pull request being worked on by an AI agent.

The agent will read your response to decide whether to make another round of changes or stop.
Your job is to distinguish between BLOCKERS and OBSERVATIONS.

BLOCKER — a specific defect the agent can fix with a code change:
- A new code path added in this PR with no test coverage at all
- A high-severity security signal in code the agent introduced
- A typosquatted or vulnerable dependency the agent added
- A high-severity anomaly that indicates a correctness bug (not an architecture preference)

OBSERVATION — something a human reviewer should know, but not a reason to iterate:
- Design decisions and assumptions (the agent chose them deliberately)
- Low or medium anomalies (style, preference, architecture commentary)
- Test gaps in pre-existing code the agent did not touch
- Medium or low security signals

Conservatism rule: when in doubt, classify as OBSERVATION, not BLOCKER.
An agent that iterates forever on observations causes more harm than one that stops too early.
Only set `agent_should_continue` to true when blockers are present AND they are fixable
by a code change (not a human decision about design or dependencies).

Respond in JSON only — no prose before or after:
{{
  "status": "clean | has_blockers",
  "agent_should_continue": false,
  "rationale": "one sentence explaining the verdict",
  "blockers": [
    {{
      "category": "test_gap | security_signal | dependency_issue | anomaly",
      "description": "concrete instruction — what specifically must change",
      "location": "file:line or file and function"
    }}
  ]
}}

Return `"blockers": []` and `"agent_should_continue": false` when status is "clean".
Never invent blockers. If you are uncertain whether something is a blocker, it is not.

PR analysis to evaluate:

Summary of change:
{summary}

Anomalies ({anomaly_count} found):
{anomalies}

Test gaps ({test_gap_count} found):
{test_gaps}

Security signals ({security_signal_count} found):
{security_signals}

Dependency issues ({dependency_issue_count} found):
{dependency_issues}
"""
