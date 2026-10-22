# Public team-code lookup

`team.py code <ten-character-code> --format json` resolves one explicitly supplied public code.
`--cache-only` forbids network access. `team.py code-status` is an operator diagnostic, not a Web DTO.
The Python interface is `team_codes.lookup(code, network=True, scenario="interactive")`.

`team.py code-batch <codes.json> --format json` accepts a JSON list of explicitly supplied codes.
The list is bounded by the operator batch limit, preserves order/duplicates, shares cached results,
and issues only serial, paced misses. It stops new network requests after a refusal or transport
failure, continues reading fresh cache entries, and returns unresolved positions with a domain error
and `stopped_reason`. It never enumerates codes or retries a failed request automatically.

Success: `{schema:"team-code.v1",code,team,cache:{hit,fetched_at}}`. `team` uses canonical
English entity names, six complete members and Champions SP. Its `format`, `season`, `rule` and
`provenance` remain null: the official query does not establish competitive context or ownership.
Consumers supply format from their existing selector and validate against their chosen context.

Failures follow the common `ok:false/query/error` contract. Domain codes are `bad_input`,
`not_configured`, `dependency_unavailable`, `cache_miss`, `busy`, `rate_limited`, `circuit_open`,
`not_found`, `maintenance`, `authentication_failed`, `tutorial_required`, `version_changed`,
`upstream_rejected`, `network_error`, `invalid_response`, `state_unavailable`.
`error.details.schema` is `team-code.v1`; retryable refusals may include `retry_after_seconds`.
No exception includes the guest identity, token, cookie, session ID or raw official response.

## Host setup

Install `requests>=2.31` and `cryptography>=43` for network lookup. Other team commands remain
usable without these optional packages. Configure only an independently registered, tutorial-complete
guest in `~/.pokemon-champions/pc.env` as `PCUI_TEAMCODE_GUEST_UUID`. Environment variables override
the same namespace in that file. No built-in or upstream-account fallback exists.

`PCUI_TEAMCODE_STATE_DIR` defaults to `~/.pokemon-champions/team-code`, outside checkout and release
payloads. On a service host set it to a persistent directory writable by the service user.

One identity must have one official-query host. Other workstations can set
`PCUI_TEAMCODE_GATEWAY_URL=https://<your-service>/api/team/code` and optionally
`PCUI_TEAMCODE_GATEWAY_KEY` to that service's operational smoke key. Remove their local guest UUID.
The gateway accepts only a single code over HTTPS, refuses redirects, keeps the original cache
timestamp and never guesses format/season/rule. Local serial batch callers use that same endpoint;
the central host remains the authority for the identity's total budget and circuit breaker.
Local cache-only queries never contact the gateway. These settings are private operator configuration.

The official-query host's state is outside checkout and release
payloads. Every caller using one identity on a host must share this directory. Multiple hosts must
use distinct identities or route through one query host; copying one identity to independent hosts
does not produce a distributed limit. Do not sync the state DB or secret into Git/public assets.

All scenarios share an SQLite reservation/lease, rolling hour/day budgets, cache and circuit breaker.
Only cache misses consume network budget; each lookup creates a new short-lived session and makes
at most the fixed token/login/search sequence, with no automatic retry or registration call.
Concurrent different-code misses return busy. A successful canonical result is cached for one day;
missing codes are cached for one hour. Stale entries are never returned as fresh. Mapping changes
invalidate cache entries. Rejected authentication/tutorial/version blocks new requests for one day;
maintenance/unknown rejection for fifteen minutes, HTTP 429 for one hour, network errors for a minute.
Fresh cached results remain usable while the circuit is open.

Operator-only settings (positive integers): `PCUI_TEAMCODE_INTERVAL_SECONDS` (10),
`PCUI_TEAMCODE_HOURLY_LIMIT` (60), `PCUI_TEAMCODE_DAILY_LIMIT` (300),
`PCUI_TEAMCODE_CACHE_SECONDS` (86400), `PCUI_TEAMCODE_BATCH_LIMIT` (20).
These are conservative local policy choices, not known official quotas. No caller, including an
owner or AI tool, bypasses the shared official-service controls. UI copy does not expose limits.
Online operators can set `PCUI_TEAMCODE_VISITOR_DAILY_LIMIT` (30) for cache-miss imports per
visitor/device and IP. Owner/tester access bypasses this visitor allowance only, never upstream policy.
`PCUI_TEAMCODE_CLIENT_VERSION` pins the client version (initially 1.2.2); changing it does not repair
an incompatible protocol or mapping. Version rejections are not an invitation to enumerate versions.

## Consumer boundaries

Web exposes only `POST /api/team/code` with `{code,format}`. Arrays, batch options, state overrides
and force-refresh flags are rejected. The same single-code resolver is used by explicit text
submissions for imports, diagnosis and actual-set matchups; typing never performs an official lookup.
The QA tool accepts only one code literally supplied in the current question and has no discovery
or enumeration tool. All imports preserve the current team until the complete result is accepted.

Source enrichment binds all six exact species and every already known configuration field. It fills
configuration only; source authorship, performance, season and rule remain independent evidence.
Conflicts preserve the source row. Pipeline network lookups are opt-in and bounded; normal refresh
uses fresh shared cache only. POCH's code evidence has its own digest-bound staging lane; it does not
claim an image/model review. See the development source README for staging operations.

## Attribution

Wire protocol and v18 numeric entity mapping are adapted from
[Pokemon-Champions-dmg_cal](https://github.com/crazylei12/Pokemon-Champions-dmg_cal), MIT,
revision `2d1a2cdb11701ccc2afe0fb1169d9976aa665ea2`. License: `team-code-upstream-LICENSE`.
The mapping is an ID translation table; battle facts and canonical names still come from the sibling dex.
