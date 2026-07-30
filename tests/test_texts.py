from chain_health.bot import texts
from chain_health.db.models import Chain, Group
from chain_health.domain.values import ChainStatus, StatusView

# di.py sets parse_mode=HTML globally, so an unescaped "<", ">", or "&" in a
# user-supplied name breaks Telegram's HTML entity parser and the send call
# raises — which, mid-handler, rolls back whatever the transaction had just
# recorded. Every function below must escape names it interpolates.

MALICIOUS_NAME = "<3 & <b>bold</b>"
ESCAPED = "&lt;3 &amp; &lt;b&gt;bold&lt;/b&gt;"


def _status(name: str = MALICIOUS_NAME) -> ChainStatus:
    chain = Chain(id=1, group_id=1, name=name, cycle_limit_km=100, is_active=True, is_retired=False)
    return ChainStatus(chain=chain, cycle_km=10, total_km=10)


def test_onboarding_done_escapes_both_names():
    text = texts.onboarding_done(MALICIOUS_NAME, MALICIOUS_NAME)
    assert MALICIOUS_NAME not in text
    assert text.count(ESCAPED) == 2


def test_pinned_status_text_escapes_group_and_chain_names():
    group = Group(id=1, user_id=1, name=MALICIOUS_NAME, default_cycle_limit_km=100)
    view = StatusView(group=group, active=_status(), all_chains=[_status()])

    text = texts.pinned_status_text(view)

    assert MALICIOUS_NAME not in text
    assert ESCAPED in text


def test_group_detail_text_escapes_group_and_chain_names():
    group = Group(id=1, user_id=1, name=MALICIOUS_NAME, default_cycle_limit_km=100)

    text = texts.group_detail_text(group, [_status()])

    assert MALICIOUS_NAME not in text
    assert ESCAPED in text


def test_chain_detail_text_escapes_chain_name():
    text = texts.chain_detail_text(_status())

    assert MALICIOUS_NAME not in text
    assert ESCAPED in text


def test_ride_recorded_text_escapes_group_and_chain_names():
    text = texts.ride_recorded_text(MALICIOUS_NAME, _status(), 42)

    assert MALICIOUS_NAME not in text
    assert ESCAPED in text


def test_chain_retired_text_escapes_chain_name():
    chain = Chain(id=1, group_id=1, name=MALICIOUS_NAME, cycle_limit_km=100)

    text = texts.chain_retired_text(chain)

    assert MALICIOUS_NAME not in text
    assert ESCAPED in text


def test_rotation_confirmed_text_escapes_chain_name():
    text = texts.rotation_confirmed_text(MALICIOUS_NAME)

    assert MALICIOUS_NAME not in text
    assert ESCAPED in text


def test_reminder_text_escapes_chain_name():
    text = texts.reminder_text(_status())

    assert MALICIOUS_NAME not in text
    assert ESCAPED in text
