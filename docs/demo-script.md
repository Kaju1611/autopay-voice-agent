# Demo script (about 6 minutes)

1. `docker compose up --build -d`, then `docker compose ps` showing all services healthy.
2. Open the dashboard, sign in. Say: all customers are fictional, and the live call goes only to my own number.
3. **Customers:** 10 fictional customers, every payment failed.
4. Open Rahul Sharma: failed payment of Rs 2,499, no calls yet.
5. Dashboard, Mock payment provider: click `success`.
6. On Rahul's page click **Start recovery call**. The "queued" message appears instantly.
7. The call starts (my phone rings in the live demo). The agent says it is an automated assistant and asks for Rahul.
8. I confirm. The agent explains the failed Rs 2,499 payment. I say: "Yes, retry the payment."
9. The agent calls `retry_payment`, the mock provider returns SUCCESS, the agent confirms, the call ends.
10. Page refresh shows: call completed, outcome recovered, payment success, transcript, state timeline, tool-call latency, recovery timestamps.
11. Dashboard: recovery rate and the Latency panel.

Scenario 2 (decline): Priya Nair, say "No" -> outcome declined, no retry.
Scenario 3 (retry fails): click `failed` in the mock panel, start Amit Verma, agree -> the agent says the retry was unsuccessful.
Reset between runs: `docker compose run --rm migrate python -m database.seed.seed --reset`