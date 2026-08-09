"""Telegram Bot API limits.

Transport constraints, not domain rules: they belong next to the code that
formats messages, not in domain/. Hardcoding them (rather than truncating
blindly at the call site) documents *why* the cutoff is 64/200.
"""

TELEGRAM_INPUT_PLACEHOLDER_MAX_LEN = 64
TELEGRAM_CALLBACK_ANSWER_MAX_LEN = 200
