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
- Examples of the established patterns in nearby files (signatures and import structure)

Identify deviations that a reviewer should be aware of. Do not flag style differences.
Flag things that suggest the change may not fit the architecture — unusual coupling,
bypassed abstractions, patterns used in a context where they are not normally used.

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

Changed files:
{changed_files_diff}

Established patterns in neighbouring files:
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
