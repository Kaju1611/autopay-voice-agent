# Latency management

On a phone call, silence is the failure. After about a second the caller assumes the line dropped. So latency is managed on two paths:
the **tool-call path** (the agent asks the backend something while the caller waits) and the **call-start path**.

## Budgets

| Path | Target (p95) | Hard limit | If exceeded |
|---|---|---|---|
| `POST /webhooks/voice/function-call` end to end | 1000 ms | 3500 ms | the agent receives a "still being processed" message, never silence |
| `get_customer_payment_status` live lookup | - | 800 ms | falls back to the cached snapshot (`source: "cache"`) |
| `retry_payment` to payment service | 700 ms (plus gateway time) | 3000 ms | returns `PENDING`; the work is idempotent and finishes server-side |
| Dashboard "Start recovery call" acknowledgement | 100 ms | - | it only enqueues; dialling happens afterwards |
| Webhook acknowledgement (call-started, transcript, call-ended) | about 10 ms | - | processed in the background, in order |

Budgets live in `shared/utils/latency.py` (`LATENCY_BUDGETS_MS`). Violations increment `latency_budget_violations`
and show in red on the dashboard Latency panel.

## Techniques used
1. **Pre-loaded call context.** Customer name, amount and failure reason are loaded once when the call is placed and passed to the provider as dynamic variables.
   The agent can open the call and explain the problem with zero lookups. The same context is cached in Redis (`callctx:<provider_call_id>`) so tools never re-query the customer.
2. **Parallel lookups.** Starting a call reads the customer and payments with `asyncio.gather`. The gateway's customer-details endpoint queries four services at once: the time is the slowest call, not the sum.
3. **Acknowledge first, process after.** Non-interactive webhooks are accepted after one Redis `SET NX` and applied in the background.
   Events of one call are applied strictly in arrival order, and tool calls wait for earlier events of their call, so a tool call can never run before the call is marked started.
4. **Hard timeout with a graceful reply.** Tool calls run under `FUNCTION_CALL_HARD_TIMEOUT_MS`. On timeout the agent gets a message to say instead of dead air.
5. **Idempotency.** `retry_payment` runs at most once per call, so a late or repeated tool call cannot double-charge. A provider retry of the same webhook returns the stored answer instantly.
6. **Connection reuse.** One pooled keep-alive HTTP client per downstream service, a tuned database pool, pooled Redis, and a long-lived provider client.
   Each service opens its database and Redis connections at startup so the first call is not slower than later ones.
7. **Short database sessions around slow work.** `retry_payment` commits and releases its database connection before the network call.
8. **Small tool responses.** A few short fields and one sentence for the agent: less for the model to read, so a faster spoken reply.
9. **In-process mock payment provider.** The payment retry skips one network hop. A real gateway adapter would replace it behind the same interface.
10. **Indexes and single-pass queries.** Every lookup column is indexed; the dashboard overview joins three small queries in memory (no N+1).
11. **Fast server stack.** uvicorn with uvloop and httptools; access logging replaced by one structured line per request.
12. **Rate limiting in one Redis round trip** (pipelined `INCR` + `EXPIRE`), failing open if Redis is down.

## Measuring
- Every response carries `Server-Timing: app;dur=...` and `X-Request-ID`.
- Each service exposes `GET /metrics`: p50, p95, p99, max per route, plus `function:<tool>` timers, `function:<tool>:e2e` timers and `downstream:<service>` timers.
- `GET /api/analytics/latency` combines all services; the dashboard Latency panel renders it.
- Every tool call's measured latency is stored on its `function_call` event and shown on the customer page.
- Requests slower than `SLOW_REQUEST_MS` (500) are logged at WARNING with `slow=true`.

```bash
curl -s http://127.0.0.1:8000/api/analytics/latency -H "$H" | python -m json.tool | less
```

## Provider-side settings that matter more than backend tuning
The language model and voice pipeline usually dominate how fast a call feels. When connecting a real provider: use a small fast model, keep the prompt short,
write the opening line as fixed text (no model call for the first words), enable the "speak while the tool runs" filler on `retry_payment`,
and set tool timeouts above our 3.5 s hard timeout.

## What has and has not been measured
- Measured in a test environment (services as separate processes over real HTTP with real Redis, SQLite instead of PostgreSQL, mock voice provider, 300 ms simulated payment gateway):
  dashboard acknowledgement 50 to 100 ms; `confirm_identity` about 8 ms in the voice service and about 15 ms end to end; `retry_payment` about 330 ms end to end (300 ms of that is the simulated gateway).
  The automated test suite also asserts that tool-call p95 stays under the 1000 ms budget in-process.
- **Windows note:** using `localhost` instead of `127.0.0.1` added roughly 250 to 300 ms to every hop in local runs (IPv6 attempted first). Use `127.0.0.1` for local URLs.
- **Not measured:** PostgreSQL under load, the Docker setup, and any real phone network or real voice provider. Re-check with the command above after your first real call.
