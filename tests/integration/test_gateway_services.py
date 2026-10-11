import httpx

from tests.conftest import INTERNAL


async def test_exactly_ten_seeded_customers(stack):
    r = await stack.gateway.get("/api/customers")
    assert r.status_code == 200 and len(r.json()) == 10
    assert all(c["email"].endswith("@example.com") for c in r.json())


async def test_customer_lookup_and_404(stack):
    assert (await stack.gateway.get("/api/customers/CUS001")).json()["name"] == "Rahul Sharma"
    assert (await stack.gateway.get("/api/customers/NOPE")).status_code == 404


async def test_customer_validation_and_duplicate(stack):
    bad = {"customer_id": "CUS900", "name": "X", "phone": "12345", "email": "x@example.com"}
    assert (await stack.gateway.post("/api/customers", json=bad)).status_code == 422
    good = {**bad, "phone": "+910000009900"}
    assert (await stack.gateway.post("/api/customers", json=good)).status_code == 201
    assert (await stack.gateway.post("/api/customers", json=good)).status_code == 409


async def test_internal_services_reject_a_missing_token(stack):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=stack.apps["customer"]), base_url="http://x")
    assert (await c.get("/customers")).status_code == 401
    assert (await c.get("/customers", headers=INTERNAL)).status_code == 200


async def test_payment_status_and_all_failed(stack):
    pays = (await stack.gateway.get("/api/payments", params={"status": "FAILED"})).json()
    assert len(pays) == 10
    p = (await stack.gateway.get("/api/payments/PAY001")).json()
    assert p["status"] == "FAILED" and p["amount"] == 2499 and p["failure_reason"] == "INSUFFICIENT_FUNDS"


async def test_retry_success_never_charges_twice(stack):
    assert (await stack.gateway.post("/api/payments/PAY001/retry")).json()["status"] == "SUCCESS"
    again = await stack.gateway.post("/api/payments/PAY001/retry")
    assert again.json()["already_paid"] is True
    assert (await stack.gateway.get("/api/payments/PAY001")).json()["retry_count"] == 1


async def test_retry_failure_and_attempt_limit(stack):
    for _ in range(3):
        assert (await stack.gateway.post("/api/payments/PAY004/retry")).json()["status"] == "FAILED"
    assert (await stack.gateway.post("/api/payments/PAY004/retry")).status_code == 409


async def test_mock_provider_override(stack):
    await stack.gateway.post("/api/demo/payment-outcome", json={"outcome": "DECLINED"})
    assert (await stack.gateway.post("/api/payments/PAY001/retry")).json()["status"] == "DECLINED"
    await stack.gateway.post("/api/demo/payment-outcome", json={"outcome": None})


async def test_gateway_requires_auth(stack):
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=stack.gateway_app), base_url="http://g")
    assert (await anon.get("/api/customers")).status_code == 401
    bad = await anon.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401
    assert (await anon.get("/api/customers", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_gateway_rate_limits(stack, monkeypatch):
    from shared.config import reset_settings
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    reset_settings()
    codes = [(await stack.gateway.get("/api/customers")).status_code for _ in range(5)]
    assert 429 in codes
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1000")
    reset_settings()