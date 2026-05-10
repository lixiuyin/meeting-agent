"""Unit tests for CLI command parsing helpers."""

import pytest

from scripts.cli_agent import MeetingAgentCLI


def test_split_args_options_parses_values_and_flags() -> None:
    positional, options = MeetingAgentCLI._split_args_options(
        ["question", "--meeting", "1,2", "--web", "--top-k", "8"],
        value_options={"--meeting", "--top-k"},
        flag_options={"--web"},
    )

    assert positional == ["question"]
    assert options["--meeting"] == "1,2"
    assert options["--top-k"] == "8"
    assert options["--web"] is True


def test_split_args_options_raises_for_missing_value() -> None:
    with pytest.raises(ValueError, match="Missing value for option"):
        MeetingAgentCLI._split_args_options(
            ["--meeting"],
            value_options={"--meeting"},
            flag_options=set(),
        )


def test_parse_int_csv_returns_int_list() -> None:
    assert MeetingAgentCLI._parse_int_csv("1, 2,3") == [1, 2, 3]
    assert MeetingAgentCLI._parse_int_csv(None) is None
