# Voice agent setup

The voice provider sits behind one interface (`voice_service/providers/base.py`):
`create_call`, `get_call`, `end_call`. The rest of the system never imports a provider SDK. Switching provider means writing one adapter file and adding it to `providers/factory.py`.

## Option A: mock provider (default, nothing to set up)
`VOICE_PROVIDER=mock` simulates a phone call. It sends the same signed webhooks and makes the same tool calls a real provider would,
following one scripted conversation per seeded customer:

| Customer | Scripted conversation | Outcome |
|---|---|---|
| CUS001, CUS003 | agrees to retry (payment succeeds) | RECOVERED |
| CUS002 | declines | DECLINED |
| CUS004 | agrees, but the retry fails | RETRY_FAILED |
| CUS005 | asks for a human | HUMAN_AGENT |
| CUS006 | never answers | NO_RESPONSE |
| CUS007 | wants to retry later | RETRY_LATER |
| CUS008 | wrong number | WRONG_NUMBER |
| CUS009 | says they already paid | ALREADY_PAID |
| CUS010 | asks why it failed, then retries | RECOVERED |

Use the dashboard's mock payment switch (`success`, `failed`, `declined`) to force the retry result for any customer.
No phone is dialled and no API key is needed. The scripts are in `voice_service/scenarios.py`.

## Option B: a real provider (not yet tested)
The Retell adapter (`providers/retell.py`) is written against Retell's documented create-call API but has **not been run against a real account**.
The full walk-through, the safety checklist and the list of things to confirm are in [step-12-live-call.md](step-12-live-call.md).
Summary of what it needs:

1. A provider account, an API key, an agent id, and an outbound number that can call your country.
2. `.env`: `VOICE_PROVIDER=retell`, `VOICE_PROVIDER_API_KEY`, `VOICE_PROVIDER_AGENT_ID`, `VOICE_PROVIDER_FROM_NUMBER`,
   `DEMO_PHONE_NUMBER` (a phone you own or have explicit permission to call), `AUTO_RECOVERY=false`.
3. A public tunnel to the webhook service (port 8004), for example `ngrok http 8004`.
4. The agent prompt and tools below, with the tunnel URL and `WEBHOOK_SECRET`.

### Agent opening line (fixed text, so the first words need no model call)
`Hello, this is an automated payment assistant calling regarding a recent failed autopay payment. Am I speaking with {{customer_name}}?`

### Agent prompt
```text
You are an automated AI payment assistant. Always be honest that you are an AI. Be brief: one or two short sentences per turn.
Customer: {{customer_name}}. Failed autopay payment of {{amount_formatted}} (reason: {{failure_reason_text}}).
1. After the opening question, if they confirm they are {{customer_name}}, call confirm_identity(confirmed=true) and follow its message.
   If wrong number, call confirm_identity(confirmed=false), apologise, end the call. Never reveal payment details to anyone else.
2. If they want to retry: call retry_payment and report the real result truthfully. Success: confirm, end. Failure: say it was
   unsuccessful and offer a later retry (schedule_payment_retry) or a human (request_human_agent).
3. "Later/tomorrow": schedule_payment_retry. "No": record_outcome(DECLINED), accept politely, end the call.
4. "I already paid": record_outcome(ALREADY_PAID); do not confirm or deny payment status; say the team will verify.
5. "Why did it fail?": record_outcome(INFORMATION_PROVIDED), explain the reason, ask again.
6. Wants a person: request_human_agent; say a human will call back (never claim a live transfer).
NEVER ask for or accept card numbers, CVV, passwords, PINs or OTPs. NEVER say a payment succeeded unless a tool returned SUCCESS.
NEVER pretend to be human.
```

### Tools
Each tool is a `POST` to `https://<tunnel-host>/webhooks/voice/function-call` with header `X-Webhook-Secret: <WEBHOOK_SECRET>`.
Set each tool timeout above 3.5 seconds (our hard timeout). Do not add `customer_id` or `payment_id` parameters: ids come from the server-side call context.

| Tool | Parameters |
|---|---|
| confirm_identity | `confirmed` (boolean) |
| retry_payment | none (enable "speak while running") |
| schedule_payment_retry | `retry_at` (optional ISO-8601) |
| request_human_agent | none |
| record_outcome | `outcome`: DECLINED, ALREADY_PAID or INFORMATION_PROVIDED |
| get_customer_payment_status | none (optional; the dynamic variables already cover the opening) |
| end_call | none (also add the provider's built-in end-call tool) |

### Lifecycle webhooks
Send the provider's call-started and call-ended events to `https://<tunnel-host>/webhooks/voice/retell`.

## Webhook security
The webhook service accepts a request only if it carries a valid `X-Webhook-Signature` (HMAC-SHA256 of the body with `WEBHOOK_SECRET`) or the shared secret in `X-Webhook-Secret`.
Duplicate events are ignored (Redis plus a unique `event_id` in the database).

## Adding another provider
1. Create `voice_service/providers/<name>.py` with a class that extends `VoiceProvider`.
2. Add a branch for it in `providers/factory.py`.
3. Map the provider's webhook payloads to the shapes in `api.md` (add a route in `webhook_service/main.py` if its envelope differs).
