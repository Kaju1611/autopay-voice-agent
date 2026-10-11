import pytest

SCENARIOS = [
    # customer, expected call outcome, expected final payment status
    ("CUS001", "RECOVERED", "SUCCESS"),
    ("CUS002", "DECLINED", "FAILED"),
    ("CUS003", "RECOVERED", "SUCCESS"),
    ("CUS004", "RETRY_FAILED", "FAILED"),
    ("CUS005", "HUMAN_AGENT", "FAILED"),
    ("CUS006", "NO_RESPONSE", "FAILED"),
    ("CUS007", "RETRY_LATER", "FAILED"),
    ("CUS008", "WRONG_NUMBER", "FAILED"),
    ("CUS009", "ALREADY_PAID", "FAILED"),
    ("CUS010", "RECOVERED", "SUCCESS"),
]


async def latest_call(stack, cid):
    calls = (await stack.gateway.get("/api/calls", params={"customer_id": cid})).json()
    assert calls, f"no call recorded for {cid}"
    return (await stack.gateway.get(f"/api/calls/{calls[0]['call_id']}")).json()


@pytest.mark.parametrize("cid,outcome,pay_status", SCENARIOS)
async def test_every_scenario(stack, cid, outcome, pay_status):
    r = await stack.run_recovery(cid)
    assert r.status_code == 202 and r.json()["status"] == "QUEUED"
    call = await latest_call(stack, cid)
    assert call["status"] == "COMPLETED" and call["outcome"] == outcome
    payment = (await stack.gateway.get("/api/payments", params={"customer_id": cid})).json()[0]
    assert payment["status"] == pay_status


async def test_successful_recovery_full_record(stack):
    """Failed payment -> recovery request -> call -> customer agrees -> retry -> SUCCESS -> recovery recorded."""
    await stack.run_recovery("CUS001")
    call = await latest_call(stack, "CUS001")
    states = [e["payload"]["to"] for e in call["events"] if e["event_type"].startswith("STATE:")]
    assert states == ["CALL_INITIATED", "CUSTOMER_ANSWERED", "IDENTITY_CONFIRMED", "PAYMENT_PROBLEM_EXPLAINED",
                      "RETRY_PAYMENT", "PAYMENT_RESULT", "COMPLETED"]          # every transition stored
    assert "automated payment assistant" in call["transcript"] and "was successful" in call["transcript"]
    tools = [e for e in call["events"] if e["event_type"] == "function_call"]
    assert {"confirm_identity", "retry_payment", "end_call"} <= {e["payload"]["name"] for e in tools}
    assert all("latency_ms" in e["payload"] for e in tools)
    attempts = {a["action"]: a["result"] for a in
                (await stack.gateway.get("/api/recovery", params={"customer_id": "CUS001"})).json()}
    assert attempts["RETRY_PAYMENT"] == "SUCCESS" and attempts["CALL_COMPLETED"] == "RECOVERED"
    a = (await stack.gateway.get("/api/analytics")).json()
    assert a["calls_attempted"] == 1 and a["successful_recoveries"] == 1
    assert a["recovery_rate"] == 100.0 and a["failed_payments"] == 9


async def test_declined_does_not_retry_the_payment(stack):
    await stack.run_recovery("CUS002")
    assert (await stack.gateway.get("/api/payments/PAY002")).json()["retry_count"] == 0


async def test_retry_later_schedules_the_payment(stack):
    await stack.run_recovery("CUS007")
    assert (await stack.gateway.get("/api/payments/PAY007")).json()["scheduled_retry_at"] is not None


async def test_only_the_authorised_number_is_dialled(stack):
    await stack.run_recovery("CUS001")
    dialled = [c["req"].to_number for c in stack.provider.calls.values()]
    assert dialled == ["+919999999999"]          # never the customer's fictional phone


async def test_no_call_without_permission(stack):
    from sqlalchemy import update

    from shared.db import sessionmaker
    from shared.models import Customer
    async with sessionmaker()() as s:
        await s.execute(update(Customer).where(Customer.customer_id == "CUS001").values(call_permission=False))
        await s.commit()
    await stack.run_recovery("CUS001")
    assert (await stack.gateway.get("/api/calls")).json() == []
    attempts = (await stack.gateway.get("/api/recovery", params={"customer_id": "CUS001"})).json()
    assert any(a["action"] == "RECOVERY_SKIPPED" and "NO_CALL_PERMISSION" in a["result"] for a in attempts)


async def test_duplicate_requests_place_one_call(stack):
    from recovery_worker import worker
    await stack.gateway.post("/api/recovery/CUS001")
    await stack.gateway.post("/api/recovery/CUS001")
    await worker.ensure_group()
    await worker.consume_once(block_ms=10)
    await stack.provider.wait_idle()
    assert len((await stack.gateway.get("/api/calls")).json()) == 1


async def test_customer_details_and_overview(stack):
    await stack.run_recovery("CUS003")
    d = (await stack.gateway.get("/api/customers/CUS003/details")).json()
    assert d["customer"]["customer_id"] == "CUS003" and d["calls"] and d["payments"][0]["status"] == "SUCCESS"
    assert d["latest_call"]["transcript"]
    ov = {r["customer_id"]: r for r in (await stack.gateway.get("/api/analytics/overview")).json()}
    assert len(ov) == 10 and ov["CUS003"]["recovery_status"] == "RECOVERED"
    assert ov["CUS001"]["recovery_status"] == "NOT_CONTACTED"


async def test_latency_metrics_are_exposed(stack):
    await stack.run_recovery("CUS001")
    m = (await stack.gateway.get("/api/analytics/latency")).json()
    assert "function:retry_payment" in m["voice"]["routes"]
    assert "function:retry_payment:e2e" in m["webhook"]["routes"]


async def test_tool_calls_stay_within_the_latency_budget(stack):
    """In-process, the whole tool-call path must stay far below the 1s dead-air threshold."""
    from shared.utils.latency import LATENCY_BUDGETS_MS, recorder
    for cid in ("CUS001", "CUS003", "CUS010"):
        await stack.run_recovery(cid)
    snap = recorder.snapshot()["routes"]
    budget = LATENCY_BUDGETS_MS["POST /webhooks/voice/function-call"]
    for name in ("function:retry_payment", "function:confirm_identity", "function:retry_payment:e2e"):
        assert snap[name]["p95"] < budget, name
    assert recorder.counters.get("function_call_timeout", 0) == 0