import pytest

from shared.config import get_settings
from shared.utils import state_machine as sm
from shared.utils.latency import LatencyRecorder
from shared.utils.security import sign, verify_webhook


def test_valid_happy_path():
    path = [sm.CALL_INITIATED, sm.CUSTOMER_ANSWERED, sm.IDENTITY_CONFIRMED, sm.PAYMENT_PROBLEM_EXPLAINED,
            sm.RETRY_PAYMENT, sm.PAYMENT_RESULT, sm.COMPLETED]
    for a, b in zip(path, path[1:]):
        sm.assert_transition(a, b)


@pytest.mark.parametrize("a,b", [(sm.CALL_INITIATED, sm.RETRY_PAYMENT), (sm.COMPLETED, sm.CUSTOMER_ANSWERED),
                                 (sm.DECLINED, sm.RETRY_PAYMENT), (sm.CUSTOMER_ANSWERED, sm.RETRY_PAYMENT)])
def test_invalid_transitions_are_rejected(a, b):
    with pytest.raises(sm.InvalidTransition):
        sm.assert_transition(a, b)


def test_every_state_can_reach_completed():
    for state, nxt in sm.TRANSITIONS.items():
        if state != sm.COMPLETED:
            assert sm.COMPLETED in nxt or any(sm.COMPLETED in sm.TRANSITIONS[n] for n in nxt)


def test_percentiles():
    r = LatencyRecorder()
    for ms in range(1, 101):
        r.observe("GET /x", ms)
    snap = r.snapshot()["routes"]["GET /x"]
    assert snap["p50"] in (50, 51) and snap["p95"] in (95, 96) and snap["max"] == 100


def test_webhook_signature_rules():
    body = b'{"a":1}'
    secret = get_settings().webhook_secret
    assert verify_webhook({"x-webhook-signature": sign(body)}, body)
    assert not verify_webhook({"x-webhook-signature": "sha256=bad"}, body)
    assert verify_webhook({"x-webhook-secret": secret}, body)
    assert not verify_webhook({"x-webhook-secret": "wrong"}, body)
    assert not verify_webhook({}, body)