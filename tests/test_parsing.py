import re

import pytest

from chain_health.bot.parsing import NUMERIC_MESSAGE_RE, parse_positive_float

ACCEPTED = [
    ("1", 1.0),
    ("0.5", 0.5),
    ("1,5", 1.5),
    (" 40 ", 40.0),
    ("999.99", 999.99),
    ("1000", 1000.0),
]

REJECTED = [
    None,
    "",
    "0",
    "0,0",
    "-5",
    "abc",
    "1e3",
    "inf",
    "nan",
    "1_000",
    "1.2.3",
    "12345678",
    "١٢",  # Arabic-indic digits
]


@pytest.mark.parametrize(("raw", "expected"), ACCEPTED)
def test_parse_positive_float_accepts_valid_input(raw, expected):
    assert parse_positive_float(raw) == expected


@pytest.mark.parametrize("raw", REJECTED)
def test_parse_positive_float_rejects_invalid_input(raw):
    assert parse_positive_float(raw) is None


def test_max_value_rejects_above_bound():
    assert parse_positive_float("1001", max_value=1000.0) is None
    assert parse_positive_float("1000", max_value=1000.0) == 1000.0


def test_max_value_none_means_unbounded():
    assert parse_positive_float("999999.999", max_value=None) is not None


@pytest.mark.parametrize("raw", ["0", "12345", "12.345", " 40 ", "1,5"])
def test_routing_regex_is_wider_than_the_parser(raw):
    """These all reach the handler (get a retry message) instead of being
    silently dropped — the earlier bug had the regex and the parser diverge.
    """
    assert re.match(NUMERIC_MESSAGE_RE, raw) is not None


@pytest.mark.parametrize("raw", ["Шоссе", "/status", "1 цепь", "", "abc123"])
def test_routing_regex_ignores_plain_text(raw):
    assert re.match(NUMERIC_MESSAGE_RE, raw) is None
