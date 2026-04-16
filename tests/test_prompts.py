"""Structural tests for pr_impact/prompts.py."""

import pytest

import pr_impact.prompts as prompts

EXPECTED_PROMPTS = [
    "PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS",
    "PROMPT_ANOMALY_DETECTION",
    "PROMPT_SECURITY_SIGNALS",
    "PROMPT_TEST_GAP_ANALYSIS",
    "PROMPT_SEMANTIC_EQUIVALENCE",
    "PROMPT_VERDICT",
]


class TestPromptConstantsExist:
    """Verify all prompt constants are defined as non-empty strings."""

    @pytest.mark.parametrize("name", EXPECTED_PROMPTS)
    def test_prompt_exists_and_is_nonempty_string(self, name):
        assert hasattr(prompts, name), f"Missing prompt: {name}"
        value = getattr(prompts, name)
        assert isinstance(value, str)
        assert len(value) > 0


class TestPromptPlaceholders:
    """Verify each prompt contains its expected placeholders."""

    def test_summary_decisions_assumptions_placeholders(self):
        text = prompts.PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS
        assert "{changed_files_diff}" in text
        assert "{blast_radius_signatures}" in text

    def test_anomaly_detection_placeholders(self):
        text = prompts.PROMPT_ANOMALY_DETECTION
        assert "{changed_files_diff}" in text
        assert "{changed_files_before_signatures}" in text
        assert "{neighbouring_signatures}" in text

    def test_security_signals_placeholders(self):
        text = prompts.PROMPT_SECURITY_SIGNALS
        assert "{changed_files_diff}" in text
        assert "{pattern_signals}" in text
        assert "{file_context}" in text

    def test_test_gap_analysis_placeholders(self):
        text = prompts.PROMPT_TEST_GAP_ANALYSIS
        assert "{changed_files_diff}" in text
        assert "{test_files}" in text

    def test_semantic_equivalence_placeholders(self):
        text = prompts.PROMPT_SEMANTIC_EQUIVALENCE
        assert "{changed_files_diff}" in text
        assert "{signatures_before_after}" in text

    def test_verdict_placeholders(self):
        text = prompts.PROMPT_VERDICT
        for placeholder in [
            "{summary}",
            "{anomaly_count}",
            "{anomalies}",
            "{test_gap_count}",
            "{test_gaps}",
            "{security_signal_count}",
            "{security_signals}",
            "{dependency_issue_count}",
            "{dependency_issues}",
        ]:
            assert placeholder in text, f"Missing placeholder: {placeholder}"


class TestPromptsFormattable:
    """Verify prompts can be formatted with dummy values without KeyError."""

    def test_summary_decisions_assumptions_formattable(self):
        result = prompts.PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS.format(
            changed_files_diff="<diff>",
            blast_radius_signatures="<sigs>",
        )
        assert "<diff>" in result
        assert "<sigs>" in result

    def test_anomaly_detection_formattable(self):
        result = prompts.PROMPT_ANOMALY_DETECTION.format(
            changed_files_diff="<diff>",
            changed_files_before_signatures="<before>",
            neighbouring_signatures="<neighbours>",
        )
        assert "<diff>" in result
        assert "<before>" in result
        assert "<neighbours>" in result

    def test_security_signals_formattable(self):
        result = prompts.PROMPT_SECURITY_SIGNALS.format(
            changed_files_diff="<diff>",
            pattern_signals="<signals>",
            file_context="<ctx>",
        )
        assert "<diff>" in result
        assert "<signals>" in result
        assert "<ctx>" in result

    def test_test_gap_analysis_formattable(self):
        result = prompts.PROMPT_TEST_GAP_ANALYSIS.format(
            changed_files_diff="<diff>",
            test_files="<tests>",
        )
        assert "<diff>" in result
        assert "<tests>" in result

    def test_semantic_equivalence_formattable(self):
        result = prompts.PROMPT_SEMANTIC_EQUIVALENCE.format(
            changed_files_diff="<diff>",
            signatures_before_after="<sigs>",
        )
        assert "<diff>" in result
        assert "<sigs>" in result

    def test_verdict_formattable(self):
        result = prompts.PROMPT_VERDICT.format(
            summary="<sum>",
            anomaly_count=2,
            anomalies="<anomalies>",
            test_gap_count=1,
            test_gaps="<gaps>",
            security_signal_count=0,
            security_signals="<signals>",
            dependency_issue_count=0,
            dependency_issues="<deps>",
        )
        assert "<sum>" in result
        assert "<anomalies>" in result
        assert "<gaps>" in result
