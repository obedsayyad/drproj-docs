# APIs, events, and action contracts

## Relief API

Public origin: https://app.cproj.obed.one. No login.

| Method/path | Behavior |
|---|---|
| POST /api/requests | Validate request; require Idempotency-Key; transactionally create once; 201 or replayed original result |
| GET /api/requests | Paginated list; filter resource, urgency, status; bounded page size |
| GET /api/requests/:id | Request detail with history[] (request updates); 404 if absent |
| PATCH /api/requests/:id | Validated status transition; requires `expected_version` (integer `version` column); 409 on mismatch |
| POST /api/demo/workload | Bounded CPU report workload, enabled through a demo-mode setting |
| GET /health/live | Process liveness; no database dependency |
| GET /api/instance | Display-only identity of the answering instance: `instance_id`, `role` (primary/warm/elastic), `provider` (self_hosted/digitalocean/linode), `region`, `label`; null when unset; no database, no addresses |
| GET /health/ready | Initialization, configuration/schema compatibility, required dependencies. Body includes `maintenance` (boolean) and `maintenance_generation` (integer from `ops.runtime_flags.generation`), so automation can confirm each instance observes the flag change |
| GET /metrics | Application metrics on the diagnostics listener (not routed publicly); no credentials or business record content |

Relief request status transitions: pending to in_progress, in_progress to resolved, in_progress to pending, resolved to in_progress (reopen). Any other transition returns 422. Limits: body 8 KB (413), quantity max 10000, Idempotency-Key pattern `^[A-Za-z0-9_-]{8,128}$`.

Application responses expose instance identity and app version in a harmless response header so traffic distribution is visible. Beyond `X-Instance-Id`, the headers `X-Instance-Role`, `X-Instance-Provider`, `X-Instance-Region` and `X-Instance-Label` (region and label URL-encoded) carry the friendly identity that the relief header shows as `Answered by: <label> · <where>` (for example `Main server · self-hosted (Pune DC)`, `Backup server · DigitalOcean fra1 (Frankfurt)`, `Extra server 2 · Linode eu-central (Frankfurt)`; the raw instance id is the tooltip). The values come from `INSTANCE_ROLE`, `INSTANCE_PROVIDER`, `INSTANCE_REGION` and `INSTANCE_LABEL`, which the Ansible app role writes from the inventory (provider and slot key of `ops.instances`) and the deployment inputs (`cloud_application.<provider>.region`, `hosts.primary.location`, default `self-hosted (Pune DC)`); they are display only and never influence routing or capacity. Workload execution uses a bounded worker pool to avoid blocking health handling with synchronous CPU loops.

## Management API

Public origin: https://ops.cproj.obed.one. The external /api prefix routes to the management service. Earlier planning shorthand such as GET /overview denotes the corresponding internal route; the external form is GET /api/overview.

| Method/path | Behavior |
|---|---|
| Mutations (all) | Request bodies are capped at 8 KB for every content type: by `Content-Length` and by streamed bytes (chunked), before JSON parsing; overflow returns 413 `PAYLOAD_TOO_LARGE`. Operator idempotency is scoped by (route template, `Idempotency-Key`) and never expires. A replay with the same request hash returns the original response (202, same `OperationAccepted` body) with `Idempotent-Replayed: true` and no second dispatch; a different request hash returns 422 `IDEMPOTENCY_KEY_REUSED`; the same key on another route is independent |
| GET /api/overview | Observed/desired state, integration freshness, incidents, last successful backup. `capacity.observed` counts only instances (and the slots they occupy) that are NOT simulated; `capacity.simulated_count` reports non-deleted simulated instances separately, so simulation can never inflate observed capacity. An integration reports `fresh` only with a timestamped real sample (`observed_at`); one with no probe yet reports `unavailable` with detail `not probed in this phase` (currently HAProxy and nothing else; it stays so until P6). **Prometheus** is judged from `GET /api/v1/targets?state=active`: `fresh` (`observed_at` is the oldest `lastScrape`) only when every job in `PROMETHEUS_EXPECTED_JOBS` has at least one active target, every such target has `health` `up`, and each `lastScrape` is within `monitoring.stale_after_seconds` (default 30 s); `stale` otherwise, with a detail naming the job and age (`job ops last scrape 42 s ago`, `job ops target is down`, `job relief has no active target`); `unavailable` with detail `Prometheus is not configured` when `PROMETHEUS_URL` is unset, or `unreachable`/`timed out` when the query fails (2 s). **Providers** is derived from reconcile age: `unavailable` (detail `no successful reconcile run recorded yet`) until a succeeded `reconcile` operation exists, then `fresh` while its `finished_at` is within 3 reconcile intervals, else `stale` (detail `last reconcile N s ago`). A down target is stale even if just attempted, because its series are gone. Details never contain URLs. The ops API only reads these metrics and never acts on them |
| GET /api/instances | Paginated inventory with lifecycle, health, routing state, vcpu, memory_mb, last_seen_at, routing_weight and a required boolean `simulated` (true for rows created by a local simulation adapter; the dashboard shows a SIMULATED badge). `public_ip` and `provider_resource_id` are redacted (null) in public responses |
| GET /api/policies | Version metadata for all statuses (active, pending, failed, superseded), paginated by version |
| PATCH /api/policies | Expected current version plus typed policy changes; asynchronous activation |
| POST /api/operations | Allowed typed infrastructure command; stable idempotency key |
| GET /api/operations/:id | Project state and StackStorm execution references/results |
| GET /api/operations | Paginated operations; filter status, kind |
| GET /api/operations/:id/events | SSE progress; reconnect with `Last-Event-ID` replays durable sequence IDs |
| GET /api/incidents | Paginated; filter state (open/resolved), severity |
| GET /api/backups | Catalogue with capture_status, verification_status, `verified_at`, `is_safety_backup`, `error_code`/`error_message`, expiry, latest successful capture. The dashboard uses `verified_at` to show whether a backup is inside the 7-day restore window |
| POST /api/backups | Immediate app dump operation |
| POST /api/backups/:id/verify | Verify one backup by isolated restore test; 202 + operation_id |
| POST /api/restores | Selected backup ID; operator initiates staged recovery. 422 unless the backup is verified (`verified_at` within 7 days) and size headroom is sufficient: current database size + restored size x 1.2 must be under 450 MB (Free limit 500 MB; both copies coexist during the schema swap) |
| GET /api/tests | Test-run history (TestRun) |
| POST /api/tests | Start a configured bounded k6 scenario against the app hostname |
| POST /api/tests/:id/stop | Stop a running test |

List endpoints return `{items, next_cursor}` with `limit` (1..100, default 20) and `cursor`.

Auth: dashboard views (GETs on ops) are public. Mutating operations (POST/PATCH/PUT/DELETE on ops.cproj.obed.one) require the shared operator password via HTTP Basic auth (`opsBasic`, enforced by Caddy); missing or wrong credentials return 401. Audit actor is `operator`. The relief app has no login.

Long-running POST/PATCH actions return 202 and operation_id. GET operation returns projected state plus freshness; do not report a successful state merely because StackStorm accepted a launch.

No arbitrary shell text, provider API URL, filesystem path, Terraform module path, SSH target, or unbounded load-test target is accepted from a browser.

## Common errors

Errors use RFC 9457 `application/problem+json` with a stable `code`. 202 means accepted work.

| Status | Meaning |
|---|---|
| 400 | Malformed input |
| 401 | Missing or invalid operator credentials on an ops mutation |
| 404 | Absent resource |
| 409 | Version conflict (`expected_version`), capacity conflict, or `IDEMPOTENCY_IN_FLIGHT` (original request still uncommitted after the bounded 2 s lock wait; `Retry-After: 2`) |
| 413 | Body over 8 KB |
| 422 | Invalid policy, unsafe state transition, unverified or too-old backup for restore, insufficient size headroom for restore (current size + restored size x 1.2 must be under 450 MB), or Idempotency-Key reused with a different payload (`IDEMPOTENCY_KEY_REUSED`) |
| 429 | Rate limit exceeded; `Retry-After` set (about 50 req/10 s general, 20 POST/10 s, demo workload 5/10 s). The configured test host (k6 VM) is exempt from per-IP limits |
| 503 | Maintenance write block: `Retry-After: 60` (`maintenance.retry_after_seconds`), `Cache-Control: no-store`, code `MAINTENANCE_WRITES_BLOCKED`, enforced on every write path including direct-IP access. If the maintenance flag cannot be read: code `DEPENDENCY_UNAVAILABLE`, writes fail closed. Also dependency/control state unavailable and a full demo work queue |

Idempotency: the insert runs with `SET LOCAL lock_timeout = '2s'`. A competitor still uncommitted after 2 s yields 409 `IDEMPOTENCY_IN_FLIGHT` with `Retry-After: 2`. Once it has committed, a matching request hash replays the original status and body with `Idempotent-Replayed: true`; a different payload returns 422 `IDEMPOTENCY_KEY_REUSED`. API responses must never contain machine secrets.

Health consumers: HAProxy checks only `/health/live` (no database, so a Supabase outage never removes a backend). `/health/ready` is used by the dashboard, Prometheus and StackStorm admission and never removes a backend.

Operation statuses: queued, running, waiting, verifying, paused, reconciling, cancelling, cancelled, succeeded, failed. An operator stop/cancel request is a separate intent that reconciles side effects; it is not equivalent to confirmed deletion or rollback. A terminal no-op succeeds with an explicit reason.

Operation kinds: activate_standby, scale_out, scale_in, primary_failure, failback, provision_and_deploy, standby_repair, reconcile, apply_policy, create_backup, verify_backup, restore_application, start_load_test, stop_load_test.

Maintenance exemption: `POST /api/demo/workload` is CPU-only and never acquires a database connection (proved by a unit test with a throwing pool), so it has nothing to drain and is not subject to maintenance admission; it stays usable during maintenance to drive load. Every other write calls `ops.app_admit_write()`.

Maintenance write admission: inside every write transaction (including direct-IP requests and the background key purge) the app calls `ops.app_admit_write()`, which reads the flag row `FOR SHARE` under a 2 s `lock_timeout`. A flag change therefore waits for in-flight writes to commit (drain), and a write that meets an uncommitted flip waits for it and then sees the committed value. Any read error, lock timeout or non-boolean result fails closed with 503 `DEPENDENCY_UNAVAILABLE`. `maintenance_generation` is the same row's `generation`.

Operation progress events (`GET /api/operations/:id/events`): each SSE `data` payload is a full `spec/events.schema.json` envelope (`service_id` `operations`, `observed_at` = row time, `policy_version` = the operation's captured version, `sequence` = SSE id, `operation_id`, `severity`). The row's `status`, `step` and `message` travel in `measurements`. `sequence` is a global identity, strictly increasing and so monotonic within each operation. Dispatch outcomes use schema event types with the detail in `step`; the operation's own status stays `queued` until automation advances it:

| Situation | event_type | step |
|---|---|---|
| Accepted by the API | `operation.started` | `accepted` |
| Handed to StackStorm | `operation.started` | `dispatched` |
| StackStorm not configured | `operation.waiting` | `awaiting_automation` |
| StackStorm launch failed | `operation.failed` | `dispatch_failed` |

## Internal events

Use the schema in spec/events.schema.json. Required envelope: event_id, event_type, source, service_id, observed_at, policy_version, measurements. Optional instance_id, operation_id, condition_fingerprint, severity, and `sequence` (durable integer, used as the SSE event id). Source values: dashboard, automation, reconciliation, alertmanager, timer.

Examples: app.cpu.high, app.cpu.low, app.primary.unhealthy, app.primary.recovered, app.memory.saturated, infrastructure.provision.failed, infrastructure.provision.exhausted, infrastructure.drain.timeout, backup.failed, metrics.stale, certificate.renewal_failed, control_state.unavailable, control_state.recovered, standby.unhealthy, standby.repaired, policy.apply.requested, reconcile.requested, operation.* and restore.* lifecycle events.

Alertmanager sends events to an authenticated internal ingress/adapter. The browser never receives StackStorm credentials. Map external alerts into the canonical envelope; validate source/version, freshness, and actionable status.

An alert proposes intent. StackStorm checks current reality, lease, slot, maintenance, redundancy, cooldown, and policy before changing capacity. Deduplication is scoped to an active intent; it must not permanently suppress later genuine overload.

## Action contracts

| Action | Inputs | Required result |
|---|---|---|
| acquire_lease / renew_lease / release_lease | operation, app key, fencing generation (renew/release) | Lease holder and generation; `acquired: false` while another live lease exists; an expired lease is taken over only by a `reconcile` operation (slot_conflict otherwise); old generation gets `lease_lost` |
| reserve_slot | operation, slot key, provider | Slot state reserved with generation; counted against the per-provider cap |
| release_slot | slot key, generation | Slot free only after confirmed absence of the resource |
| terraform_plan_apply | slot workspace, operation/attempt, `-lock-timeout=30s` | Plan summary and apply result for that slot's state only |
| terraform_output | slot workspace | Actual provider ID and IP from state |
| provision_vm | operation/attempt, slot/generation, provider, size/image/region | Actual provider ID, IP, ownership evidence (tags project, slot, op, attempt) |
| delete_vm | exact provider/state/resource identity | Confirmed absence before freeing slot; never by broad tag search |
| redeploy_app | instance, approved digest, config revision | Running version, digest match |
| check_readiness | instance, deadline | /health/ready result and dependency checks |
| observe_haproxy | backend/server, window | Server status, weights, 5xx ratio, p95 from HAProxy |
| apply_routing | desired membership/mode/weights, config generation | Validated persistent config (server-state file) and observed HAProxy result |
| set_maintenance | operation, reason | Maintenance flag set; writes blocked |
| clear_maintenance | operation | Flag cleared and confirmed |
| create_backup | approved app scope, operation | Artifact, checksum, size, capture times |
| verify_backup | catalogue ID, operation | Isolated restore result and integrity evidence |
| restore_backup | catalogue ID, controlled target (invoked by workflow restore_application) | Restore status and integrity evidence |
| apply_policy | policy version, changes | Activation phase result (validating to completed) |
| update_ops_state | operation, projected state | Durable projected state and event sequence |

Drain is performed through apply_routing (draining mode) with a deadline; drain timeout pauses the operation and raises an incident. Lease: each side-effecting action asserts `assert_lease(op_id, generation)` first.

Never interpret a result string as a shell command. Job output is structured, bounded, and redacted.
