# Official API and runtime notes

Reviewed 2026-10-08. Public documentation is authoritative for the target account/version.

## [Management overview](https://developers.deepgram.com/reference/deepgram-api-overview)

Token authentication to /v1 Management APIs.

## [Usage breakdown](https://developers.deepgram.com/reference/manage/usage/breakdown/get)

start/end dates and enum grouping dimensions.

## [Billing breakdown](https://developers.deepgram.com/reference/manage/billing/breakdown/get)

Billing grouping is an array; dollar values describe billing costs.

## [Balances](https://developers.deepgram.com/reference/manage/billing/list)

Outstanding balances are a separate resource from period billing.

## Boundaries

Python 3.10+. `DEEPGRAM_API_KEY` or explicit `DEEPGRAM_API_KEY_FILE`. Use a key with management scopes for the requested resource (project, usage or billing access). No legacy /tmp credential discovery; `doctor` checks local credential availability without a network call.

Read-only Management API; no transcription, audio download, key creation or purchase. Request lists and purchases return the requested page, and summaries cover that response. Generic get accepts provider parameters directly, so the agent must preserve bounded scope. Currency and billing semantics follow Deepgram.

HTTP helpers do not follow redirects or automatically retry writes. A timeout can mean an unknown outcome; inspect the target before retrying. Secret-field redaction is defense in depth, not a guarantee that arbitrary free text is safe to publish.
