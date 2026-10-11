# API reference

Interactive docs: http://127.0.0.1:8000/docs. All gateway routes need `Authorization: Bearer <token>` except login and `/api/health/all`.

```bash
# Git Bash: log in using the password from .env, keep the token in $H
PW=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2- | tr -d '\r')
TOKEN=$(curl -s -XPOST http://127.0.0.1:8000/api/auth/login -H 'content-type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PW\"}" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
H="Authorization: Bearer $TOKEN"
```

## Gateway
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/login` | `{username, password}` returns `{access_token}` |
| GET | `/api/health/all` | health of every service (no login needed) |
| GET | `/api/analytics/latency` | p50/p95/p99 per route and agent tool, from all services |
| GET | `/api/customers/{id}/details` | customer, payments, calls, recovery attempts and latest call, fetched in parallel |
| POST | `/api/demo/payment-outcome` | `{"outcome": "SUCCESS" or "FAILED" or "DECLINED" or null}` forces the mock provider |

## Customers
| Method | Path | Notes |
|---|---|---|
| GET | `/api/customers` | list |
| GET | `/api/customers/{customer_id}` | 404 if unknown |
| POST | `/api/customers` | `customer_id` like `CUS001`, E.164 `phone`, `email`; 409 if it exists, 422 if invalid |

## Payments
| Method | Path | Notes |
|---|---|---|
| GET | `/api/payments?customer_id=&status=` | list |
| GET | `/api/payments/{payment_id}` | |
| POST | `/api/payments/{payment_id}/retry` | retries through the mock provider. Safe to repeat: an already-paid payment returns `already_paid: true`. 409 if the status is not retryable, a retry is in flight, or 3 attempts were used |
| POST | `/api/payments/{payment_id}/schedule-retry` | body `{"retry_at": ISO date or null}`, default +24h |
| POST | `/api/payments/{payment_id}/fail` | simulate an autopay failure and publish `payment.failed` |
| POST | `/api/payments/detect-failed` | publish `payment.failed` for every failed payment (ignored by the worker unless `AUTO_RECOVERY=true`) |

Payment statuses: `PENDING`, `FAILED`, `RETRY_REQUESTED`, `SUCCESS`, `DECLINED`, `CANCELLED`.

## Calls
| Method | Path | Notes |
|---|---|---|
| POST | `/api/calls/{customer_id}` | starts a call directly (202). 409 if not eligible, no failed payment, or a call is already active |
| GET | `/api/calls?customer_id=` | list, newest first |
| GET | `/api/calls/{call_id}` | status, outcome, transcript (live while running), state events, tool calls with `latency_ms` |
| POST | `/api/calls/{call_id}/end` | ask the provider to end the call (not all providers support it) |

## Recovery and analytics
| Method | Path | Notes |
|---|---|---|
| POST | `/api/recovery/{customer_id}` | queues a recovery (202). The dashboard button uses this |
| GET | `/api/recovery?customer_id=` | recovery attempts, newest first |
| GET | `/api/analytics` | totals; `recovery_rate` = successful recoveries / calls attempted, in percent |
| GET | `/api/analytics/overview` | one row per customer for the Customers table |

## Webhook service (port 8004, not behind the gateway)
Authentication: header `X-Webhook-Signature: sha256=<HMAC-SHA256 of the body using WEBHOOK_SECRET>`, or header `X-Webhook-Secret: <WEBHOOK_SECRET>`. Anything else gets 401.

| Path | Minimum body | Response |
|---|---|---|
| `POST /webhooks/voice/call-started` | `{event_id?, provider_call_id}` | `{"status": "accepted"}` or `"duplicate"` |
| `POST /webhooks/voice/transcript` | `{event_id?, provider_call_id, role: "agent" or "user", content}` | same |
| `POST /webhooks/voice/call-ended` | `{event_id?, provider_call_id, disconnection_reason, duration_ms}` | same |
| `POST /webhooks/voice/function-call` | `{name, args, call: {call_id}}` | the tool result (see below) |
| `POST /webhooks/voice/retell` | Retell envelope `{event, call}` | `{"status": ...}` |

## Agent tools
| Tool | Arguments | Effect |
|---|---|---|
| `confirm_identity` | `confirmed: boolean` | true moves to `PAYMENT_PROBLEM_EXPLAINED`; false ends as `WRONG_NUMBER` |
| `retry_payment` | none | one retry per call; returns `SUCCESS`, `FAILED`, `DECLINED` or `PENDING` |
| `schedule_payment_retry` | `retry_at?` ISO-8601 | schedules a later retry (default +24h) |
| `request_human_agent` | none | records a callback request (no live transfer) |
| `record_outcome` | `outcome`: `DECLINED`, `ALREADY_PAID` or `INFORMATION_PROVIDED` | records the customer's decision |
| `get_customer_payment_status` | none | live status, falling back to the cached snapshot |
| `end_call` | none | asks the provider to hang up |

Tool results are short on purpose: `{status or ok, message}` where `message` is a one-sentence instruction for the agent.

## Internal routes (voice service, require `X-Internal-Token`)
`POST /internal/events` and `POST /internal/functions/execute`, called only by the webhook service.

## Errors
`401` missing or invalid token or signature, `404` unknown id, `409` state conflict (see each route), `422` validation, `429` rate limit, `502` a backend service is unreachable,
`504` a backend service timed out, `500` unexpected error (response includes a `request_id` to search the logs for).
