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
