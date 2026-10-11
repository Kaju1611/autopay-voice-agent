# Demo script (about 6 minutes)

Recording tip: sign in and start the stack before you press record. The stack takes about 2 minutes to become healthy on Docker Desktop.

## Setup (off camera)
```bash
cp .env.example .env          # first time only; set ADMIN_PASSWORD and the secrets
docker compose up -d
docker compose ps             # wait until everything shows healthy
docker compose run --rm migrate python -m database.seed.seed --reset
docker compose exec redis redis-cli flushdb
```
`.env` should have `VOICE_PROVIDER=mock` for the simulated-call demo. For a live call see `step-12-live-call.md`.

## On camera
1. **Start.** Show `docker compose ps` (all services healthy) and http://127.0.0.1:8000/api/health/all.
2. **Say:** all customers are fictional, no real payment is made, and a live call would go only to my own authorised number.
3. Open the dashboard at http://127.0.0.1:5173 and sign in.
4. **Customers:** ten fictional customers, every payment `failed`.
5. **Dashboard, Mock payment provider:** click `success`.
6. Open **Rahul Sharma**: failed payment of Rs 2,499 (insufficient funds), no calls yet.
7. Click **Start recovery call**. The "queued" message appears immediately.
8. Watch the **transcript fill in live**: the agent identifies itself as an automated assistant, asks for Rahul, explains the failed payment, and asks to retry.
   The customer says "Yes, retry the payment." (In a live call, you say it.)
9. The agent calls `retry_payment`; the mock provider returns SUCCESS; the agent confirms; the call ends.
10. On the same page show: call **completed / recovered**, payment **success**, the **state timeline**, **tool calls with their latency in ms**, and the **recovery attempts with timestamps**.
11. **Dashboard:** recovery rate cards and the **Latency panel** (tool-call p50/p95 against the budget).
12. **Calls** and **Recovery** pages: the full record.

## Scenario 2: customer declines
Open Priya Nair, start a call. The customer says no. Outcome **declined**, payment still `failed`, no retry made.

## Scenario 3: retry fails
Dashboard: click `failed` in the mock panel. Start a call for Amit Verma, the customer agrees. The agent says the retry was unsuccessful and offers a later retry or a human.
Outcome **retry failed**. Click `reset` in the mock panel afterwards.

## Optional extras (each takes about 20 seconds)
- **Human requested:** Vikram Rao. **No answer:** Neha Gupta. **Retry later:** Arjun Mehta. **Wrong number:** Kavya Reddy. **Already paid:** Sanjay Patel. **Asks for explanation:** Divya Menon.
- **Safety:** try **Start recovery call** twice quickly on one customer. Only one call is placed.

## Reset between takes
```bash
docker compose run --rm migrate python -m database.seed.seed --reset
docker compose exec redis redis-cli flushdb
```
Then reload the dashboard.

## If something looks wrong on camera
| Symptom | Fix |
|---|---|
| Dashboard shows an error banner | `curl http://127.0.0.1:8000/api/health/all` to see which service is down; `docker compose logs <service>` |
| Button does nothing / no call starts | check `docker compose logs recovery-worker voice-service` |
| Customer still shows "in progress" from an earlier take | reset with the commands above |
| Latency panel is empty | run one call first; it fills in afterwards |
