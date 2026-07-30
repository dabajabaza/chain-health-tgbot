import re

# Routing only: anything that starts with a digit and contains nothing but digits
# and separators. Deliberately looser than the parser below, so "0", "12345",
# "12.345" and " 40 " all reach the handler and get a retry message instead of
# silently matching nothing (the divergence between this and the parser used to
# be the bug — keep both regexes in this one module so they can't drift again).
# [0-9] rather than \d: \d matches any Unicode decimal digit (e.g. Arabic-indic
# ١٢), which Python's float() would also happily parse — ASCII-only here keeps
# both regexes honest about "no non-ASCII digits".
NUMERIC_MESSAGE_RE = r"^\s*[0-9][0-9\s.,]{0,15}$"

_DECIMAL_RE = re.compile(r"[0-9]{1,7}(\.[0-9]{1,3})?")


def parse_positive_float(raw: str | None, *, max_value: float | None = None) -> float | None:
    """Strict positive decimal: dot or comma separator, no exponents, no
    inf/nan, no underscores, no non-ASCII digits — anything Python's ``float()``
    would accept but a human wouldn't type as a distance.
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", ".")
    if not _DECIMAL_RE.fullmatch(text):
        return None
    value = float(text)
    if value <= 0:
        return None
    if max_value is not None and value > max_value:
        return None
    return value
