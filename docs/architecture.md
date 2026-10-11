# Architecture

```text
React dashboard (nginx :5173)
        | /api
        v
API Gateway :8000  (JWT, rate limit, CORS, audit log, aggregation)
        | REST + request IDs + internal token
  +-----------+-------------+--------------+
  v           v             v              v
Customer   Payment        Voice       Recovery + Worker
:8001      :8002          :8003       :8005
           (+ mock        (provider   (Redis Streams consumer
            payment        adapter,    + recovery/analytics API)
            provider)      state            ^
                           machine,         | autopay:events stream
                           tools)           | (recovery.requested / payment.failed)
  |           |             ^
  +-----------+-------------+--> PostgreSQL

Voice provider (mock | Retell) --signed webhooks--> Webhook Service :8004 --REST--> Voice Service
Redis: event stream, locks, idempotency keys, pre-warmed call context, rate limits
```

## Services

| Service | Port | Owns | Responsibilities |
|---|---|---|---|
| api-gateway | 8000 | - | Only public API. Login (JWT), rate limiting, CORS allow-list, routing to services, audit log of writes, aggregate endpoints (customer details, latency report, health). |
| customer-service | 8001 | `customers` | Customer lookup and creation, call eligibility (call permission, autopay enabled). |
| payment-service | 8002 | `payments` | Payment states, idempotent retry (lock, attempt limit), failed-payment events, **mock payment provider**. |
| voice-service | 8003 | `calls`, `call_events` | `VoiceProvider` interface with mock and Retell adapters, conversation state machine, agent tools, applies provider events. |
| webhook-service | 8004 | (Redis only) | Verifies signatures, deduplicates events, acknowledges fast, keeps events of one call in order, serves tool calls under a hard timeout. |
| recovery-worker | 8005 | `recovery_attempts` | Consumes the Redis stream, checks eligibility, asks voice-service to place the call. Also serves recovery and analytics endpoints. |
| frontend | 5173 | - | React + Vite dashboard served by nginx, which forwards `/api` to the gateway. |
| migrate | - | schema | One-shot job: Alembic migration, then seed of 10 fictional customers. |

All services share one PostgreSQL database with strict table ownership. Services call each other over REST (with the internal token);
asynchronous work uses Redis Streams. Only the gateway (8000), the dashboard (5173) and the webhook service (8004) are published to the host.

## Recovery workflow
1. A failed payment exists. Someone clicks **Start recovery call**: `POST /api/recovery/{customer_id}` records a `QUEUED` attempt, puts an event on the stream and returns 202 immediately.
2. The worker takes a per-customer lock, checks eligibility with the customer service, then calls the voice service.
3. The voice service reads the customer and payments in parallel, dials **only `DEMO_PHONE_NUMBER`** (or simulates the call in mock mode), allows one active call per customer,
   creates the call record and caches the call context in Redis before the provider can send anything.
4. The provider sends webhooks and tool calls through the webhook service. Tools: `confirm_identity`, `retry_payment`, `schedule_payment_retry`, `request_human_agent`, `record_outcome`, `get_customer_payment_status`, `end_call`.
5. `retry_payment` calls the payment service, which calls the mock provider and returns `SUCCESS`, `FAILED` or `DECLINED`.
6. Every state change, tool call (with its latency), transcript line and recovery step is stored.

## Call state machine
```text
CALL_INITIATED -> CUSTOMER_ANSWERED -> IDENTITY_CONFIRMED -> PAYMENT_PROBLEM_EXPLAINED -> one of:
    RETRY_PAYMENT -> PAYMENT_RESULT
    RETRY_LATER | DECLINED | HUMAN_AGENT | ALREADY_PAID | INFORMATION_PROVIDED (then back to a decision)
CUSTOMER_ANSWERED -> WRONG_NUMBER
CALL_INITIATED    -> NO_RESPONSE
every path ends in COMPLETED
```
Invalid transitions are rejected (`shared/utils/state_machine.py`). Each accepted transition is stored as a `call_events` row named `STATE:<state>`.

Call outcomes: `RECOVERED`, `RETRY_FAILED`, `RETRY_LATER`, `DECLINED`, `HUMAN_AGENT`, `WRONG_NUMBER`, `NO_RESPONSE`, `ALREADY_PAID`, `PAYMENT_PENDING`, `INCOMPLETE`, `PROVIDER_ERROR`.

## Database tables
`customers`, `payments`, `calls`, `call_events`, `recovery_attempts` (see `shared/models.py`). Indexed on every lookup column used during a call.
`call_events.event_id` is unique, which makes duplicate webhook deliveries harmless.

## Safety and security
- All customer data is fictional. Seeded phone numbers are invalid placeholders and are never dialled.
- A real provider dials only `DEMO_PHONE_NUMBER` and refuses to dial if it is empty. `AUTO_RECOVERY=false` by default, so nothing dials without an explicit request.
- No real payment gateway exists anywhere in the system. Payments go to a mock provider.
- The agent never asks for card numbers, CVV, passwords or OTPs; no tool accepts such fields.
- Tools ignore customer and payment ids supplied by the model: ids come from the server-side call context.
- Secrets come only from environment variables. JWT login, internal service token, per-IP rate limiting, CORS allow-list, Pydantic validation, signed webhooks, audit log of every write.
- Duplicate protection: webhook event ids (Redis and database), one in-flight retry per payment, one retry per call, attempt limit of 3, one active call per customer.
- Structured JSON logs carry `request_id`, `correlation_id`, `customer_id`, `payment_id`, `call_id`, `provider_call_id`.

## Limitations
- One database shared by all services (table ownership is by convention).
- Webhook ordering uses an in-process task chain: correct for one webhook-service instance. Scaling out needs events of one call routed to one instance, or a partitioned stream.
- Human handoff only queues a callback; there is no live transfer.
- The real-provider path (Retell adapter, live phone call) is written but not yet tested. See `step-12-live-call.md`.
- Failed events that cannot be applied go to a Redis dead-letter list; there is no automatic replay.
