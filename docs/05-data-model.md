# Data model

Use two PostgreSQL schemas in Supabase Free: app for recoverable business data, ops for management/control records. No Supabase Auth dependency, user table, login, or RBAC is required. All timestamps are UTC timestamptz; IDs are UUIDs unless a stable slot key is more appropriate.

## Application tables

| Table | Principal fields and constraints |
|---|---|
| app.relief_requests | id, resource_type (food/water/medicine/shelter), quantity > 0, location, urgency (low/medium/high/critical), status (pending/in_progress/resolved), version integer not null default 1 (incremented on every update; PATCH sends `expected_version`, mismatch returns 409), created_at, updated_at |
| app.request_updates | id, request_id, old_status, new_status, changed_at, source; request FK stays within app |
| app.idempotency_records | key, method, route_template, request_hash, response_status, response_body, created_at, expires_at; unique(method, route_template, key) |

Request creation and its idempotency result commit in one transaction. `route_template` is the route pattern (for example `/api/requests/{id}`), not the concrete path. `request_hash` covers method, concrete path and canonical body. The key is claimed in the same transaction as the business write, using `INSERT ... ON CONFLICT (method, route_template, key) DO UPDATE ... WHERE expires_at <= clock_timestamp() RETURNING 1` (implemented form: a live key conflicts and returns no row; an expired key is taken over in place). If the business write fails (409/422), the whole transaction rolls back, including the claim, so the same key can be retried; a committed key still returns 422 `IDEMPOTENCY_KEY_REUSED` for a different request:

- Retried identical content returns the saved status and body with `Idempotent-Replayed: true`.
- Same key with a different `request_hash` returns 422.
- In-flight duplicate: run `SET LOCAL lock_timeout = '2s'` before the claim `INSERT`. If the lock timeout fires (the competitor has not committed), return 409 `IDEMPOTENCY_IN_FLIGHT` with `Retry-After: 2`. If the insert returns zero rows after the competitor committed, compare `request_hash`: same replays the original response with `Idempotent-Replayed: true`; different returns 422.
- An expired row is taken over with `ON CONFLICT DO UPDATE ... WHERE expires_at <= clock_timestamp()`.

Keys must match `^[A-Za-z0-9_-]{8,128}$` (zod in the contracts package and a CHECK on the column). Lock/unique-key handling must safely resolve concurrent identical requests.

Implementation placement (resolved engineering defaults):

- **Migration bookkeeping.** Each migration track records applied files in its own `<track>.schema_migrations` table (name, checksum): `ops.schema_migrations` and `app.schema_migrations`. The app-schema table lives in the `app` schema (so it is part of an app-only restore and is re-created consistently with it); `dr_app_runtime` has SELECT on it (`app/0002_runtime_readiness_grant.sql`) so `/health/ready` can confirm the required app migration is applied. Applied files are never edited; changes ship as new files, ordered `ops` then `app`.
- **Quantity bound.** `quantity` is limited to 1..10000 in two places that must agree: the column CHECK in `app/0001_app_schema.sql` and `MAX_QUANTITY` in the contracts package (checked against the OpenAPI schema).
- **`ops/0003_ops_api.sql` (Phase 2).** Adds `ops.operations.parameters` (typed command inputs, never secrets or paths), `ops.backups.restored_bytes` (restore headroom check), `ops.test_runs.created_at`, the durable `ops.operation_events` table, the unique index allowing one pending policy version, and `ops.finish_policy_activation()` (marks a pending version active/failed atomically; the old version stays active on failure).
- **`ops/0004_maintenance_admission.sql`.** `ops.app_admit_write()`, a VOLATILE SECURITY DEFINER function that reads `ops.runtime_flags` with `FOR SHARE`; see docs/06, Maintenance write admission.
- **`ops/0005_operation_event_envelope.sql`.** Adds `ops.operation_events.event_id` (unique UUID, the envelope `event_id`) and renames pre-release event types to the schema's names.
- **`ops/0006_operation_idempotency_scope.sql`.** Operator idempotency keys are scoped by `(route_template, key)`: adds `ops.operations.route_template`, replaces the global key uniqueness with `UNIQUE (route_template, idempotency_key)`, and checks that key and route are set together.
- **`ops/0007_keepalive_runs.sql` (Phase 3).** Append-only `ops.keepalive_runs` (`started_at`, `finished_at`, `succeeded`, `source` in `stackstorm|host_cron`, `detail` short error class, at most 200 characters, never a connection string). The StackStorm `cdr.keepalive` action inserts a row after each `SELECT 1`; `dr_ops` has SELECT and INSERT only. The ops API reads it for `cdr_supabase_keepalive_last_success_timestamp`.
- **`ops/0008_processed_events_source.sql` (Phase 3).** Adds `ops.processed_events.source` (`NOT NULL`, default `alertmanager`, same five values as the audit-event source enum) and `event_name` (at most 100 characters, the alertname). The `cdr.record_alert` action upserts one row per `(fingerprint, policy_version)` and bumps `last_seen_at` on repeats.
- **`ops/0009_capacity_control.sql` (Phase 4).** Seeds the 13 `ops.capacity_slots` rows (`warm`, `elastic-do-1..5`, `elastic-linode-1..5`, `interim-warm-digitalocean`, `interim-warm-linode`) and the `relief` row of `ops.control_leases`. Capacity state changes only through SECURITY DEFINER functions executable by the new group role `dr_automation` (the StackStorm login is granted `dr_ops` and `dr_automation`); `dr_ops` loses INSERT/UPDATE/DELETE on `capacity_slots`, `instances` and `control_leases`. Lease: `acquire_lease` (an expired lease is taken over with a new fencing generation), `renew_lease`, `assert_lease(op_id, generation)`, `release_lease`. Slots: `reserve_slot` (cap and warm-floor counted over reserved, bound, releasing and held_uncertain slots; replay-safe per operation), `bind_slot`, `mark_slot_uncertain`, `begin_slot_release` (warm-role slots protected), `release_slot` and `resolve_held_slot` (both need provider-API evidence; the latter is reconcile-only), `next_elastic_provider`, `scale_in_candidate`. Attempts: `start_attempt` records the attempt before the provider create call (`created=false` after a restart), `finish_attempt`. Projection: `advance_operation` is the one writer of operation status, step and `operation_events` for automation. A trigger enforces the immutable ceiling of 5 occupied slots per provider. `ops.instances.simulated` and `ops.operations.adapter_mode` mark local-adapter work; the providers integration ignores simulated reconcile runs. Errors use SQLSTATE `CDR01` lease_lost, `CDR02` slot_conflict, `CDR03` invalid_transition, `CDR04` bad_argument.
- **`ops/0010_lease_takeover_attempt_grants.sql` (Phase 4 review).** `acquire_lease` takes over an expired, never-released lease only when the caller's operation `kind` is `reconcile`; any other kind gets `CDR02` (`lease_expired_reconcile_only`) and must wait for reconcile, because the previous worker may still have side effects in flight. A released lease (no holder) is free for anyone. `dr_ops` loses INSERT/UPDATE/DELETE on `ops.operation_attempts` (read stays); `dr_automation` gets full access, and attempts are otherwise written only through `start_attempt`/`finish_attempt`.
- **`ops/0011_provisioning_intents.sql` (Phase 4.2).** `ops.operation_attempts.checkpoints_at` and a replaced `ops.start_attempt` that stores every readiness checkpoint and the deadline from the policy version the operation captured (return type adds `checkpoints_at`; `created=false` after a restart). `ops.instances.power_state` (`on`, `off`, `unknown`) with `ops.set_instance_state` (fenced, bound slots only; lifecycle `provisioning`, `deploying`, `ready` or `failed`). `ops.open_operation(kind, source, fingerprint, event_name)` opens an automation-originated intent (kinds `scale_out`, `scale_in`, `primary_failure`, `standby_repair`, `reconcile`, `activate_standby`), captures the active policy version (none active: `CDR04`), and is idempotent on the fingerprint through `ops.processed_events` while the linked operation is not terminal. `ops.open_incident(fingerprint, severity, source, description, resources, operation)` upserts the single open incident per fingerprint and appends an `incident_events` row (`opened` or `repeated`). All four are `EXECUTE` for `dr_automation` only; `dr_ops` is denied.
- **`ops/0012_reconcile_failover.sql` (Phase 4.3).** `ops.runtime_flags.metrics_stale_since` with `ops.set_metrics_stale(boolean)`; `ops.set_routing(op, lease generation, slot, slot generation, mode, weight)` (fenced routing projection: `active`/`backup` need a ready instance, `draining` marks the instance `draining`, returning to service clears it); `ops.hold_orphan_slot` and `ops.reconcile_attempt` (both reconcile-only: hold a free slot `held_uncertain` for an unmatched tagged resource without freeing or deleting anything; finish a dead operation's attempt along the normal transitions); `ops.supersede_queued(kind, by)` (queued operations of a kind become succeeded no-ops `superseded`); `ops.resolve_incident(fingerprint, message, operation)`. `ops._operation_transition_ok` now allows `waiting` and `verifying` to `reconciling`, and `ops.advance_operation` lets a re-dispatch replace `stackstorm_execution_id` only while the operation is `reconciling`. All new functions are `EXECUTE` for `dr_automation` only.
- **`ops/0013_standby_policy.sql` (Phase 4.4).** `ops.scale_in_candidate()` (while the original warm slot is bound and ready and an `interim-warm-*` slot is bound, the interim slot is the surplus warm-role slot and the scale-in candidate; otherwise the newest bound elastic slot; a lone warm-role slot is never a candidate); `ops.set_instance_state` now lets a `standby_repair` or `reconcile` operation record the state of a bound warm-role slot an earlier operation provisioned; `ops.set_policy_phase(op, version, phase)` and `ops.finish_policy_apply(op, version, success, result)` (only the `apply_policy` operation of that pending version; failure keeps the old version active; cap reductions below the occupied count are recorded as a pending transition); `ops.fail_pending_policy(op, lease generation, target op, reason)` (reconcile closes the pending version of an `apply_policy` operation that no longer runs). The new functions are `EXECUTE` for `dr_automation` only; `dr_automation` also gets `SELECT` on `ops.policy_versions`.

Do not expire a key while its request is in progress. Use a configurable 24-hour initial retention for completed idempotency results; request records themselves remain until deliberately removed. Replaying a key outside its documented retention window is not a permanent deduplication guarantee.

### Relief request status transitions

| From | To | Meaning |
|---|---|---|
| pending | in_progress | Work started |
| in_progress | resolved | Completed |
| in_progress | pending | Returned to the queue |
| resolved | in_progress | Reopened |

Any other transition (including same-status and pending to resolved) returns 422. Each accepted change increments `version` and writes an app.request_updates row.

## Management tables

| Table | Purpose |
|---|---|
| ops.policy_versions | id, monotonically increasing version, JSON configuration, status (pending/active/failed/superseded), validation/apply result, created_at, activated_at |
| ops.capacity_slots | key (one slot per possible VM: `warm`, plus `elastic-do-1..5` and `elastic-linode-1..5`, plus a temporary `interim-warm-<provider>` while the warm slot is `held_uncertain`; the warm slot counts within its provider's cap, so at most 4 elastic slots are usable on the warm provider), provider, role (warm/elastic), generation, state, bound_instance_id, lease_operation_id; each slot has its own Terraform state/workspace |
| ops.instances | internal UUID, slot key, provider, provider_resource_id, role, public_ip, vcpu, memory_mb, image_digest, configuration_revision, lifecycle/health/routing states, routing_weight (integer 0..256), slot_state (mirrors the bound slot's state), last_seen_at |
| ops.operations | id, kind, idempotency key, request hash, captured policy version, automation revision, image digest, StackStorm execution ID, projected status, current_step, timestamps, outcome |
| ops.operation_events | sequence (GENERATED ALWAYS AS IDENTITY, global and strictly increasing, therefore monotonic within each operation; the SSE event id), event_id (UUID), operation_id, event_type (events.schema enum value), status, step, message, source, occurred_at; replayed by `sequence > Last-Event-ID` |
| ops.operation_attempts | operation_id, attempt number, provider, StackStorm action ID, checkpoint/deadline times, error code, result references |
| ops.incidents | id, condition fingerprint, severity, affected resource references, open/resolved state, source, description, timestamps, baseline_p95_seconds (nullable; the pre-failure p95 baseline of the `primary.unhealthy:relief` incident, set by `ops.set_incident_baseline`, used by the failback p95 gate) |
| ops.incident_events | incident_id, event kind, message, source, timestamp, operation reference |
| ops.backups | id, artifact path, source schema/database identifier, capture start/finish, schema revision, bytes, checksum, capture_status, verification_status, error_code, error_message, verified_at, expired_at, is_safety_backup; retention is computed from capture finish plus the policy retention days, not stored as a deadline |
| ops.recovery_runs | selected backup, safety backup, isolated target, validation results, maintenance state, restore/rollback result, operation reference |
| ops.audit_events | source (dashboard/automation/reconciliation/alertmanager/timer; same values as the event `source` enum), action, object, old/new version or values, operation ID, timestamp; actor is the fixed value `operator` for dashboard mutations behind the shared management basic-auth, otherwise empty; every HAProxy Runtime API mutation by the real routing adapter is an `haproxy.command` row written by `ops.record_haproxy_command` (object `lb:be_app/<server>`, old state, new state, the command, the membership dump name, `adapter_mode`) |
| ops.haproxy_commands | id, operation_id, lease generation, command text (`set server be_app/...`), status `pending`/`applied`/`abandoned`, dump name (set when applied), backfilled flag, created/completed timestamps; the `pending` row is inserted in the same database call that asserts the lease, before the Runtime API command is sent (ops/0016) |
| ops.control_leases | application key, holder operation, lease expiry, fencing generation |
| ops.runtime_flags | maintenance state, incident reference, generation, last changed; remains outside app restores |
| ops.test_runs | k6 scenario, bounded rate/duration, lifecycle, results location, summary metrics |
| ops.processed_events | source (same enum as audit events), fingerprint, policy version, event name (alertname), timestamps, active intent/operation reference; coalesce repeated notifications. Phase 3 records only Alertmanager alerts (`source=alertmanager`); no action is taken from the rows before Phase 4 |

Ops data is a projection of workflows, not a replacement for StackStorm's native execution database. Store native execution references rather than duplicating every log line.

## Canonical enums

One vocabulary, mirrored in the OpenAPI and event schemas.

| Enum | Values |
|---|---|
| Instance lifecycle | reserved, provisioning, deploying, ready, draining, deleting, deleted, failed, unknown (existence uncertain; the slot stays counted) |
| Routing mode | none, backup, active, draining |
| Slot state | free, reserved, bound, releasing, held_uncertain |
| Operation status | queued, running, waiting, verifying, paused, reconciling, cancelling, cancelled, succeeded, failed |
| Operation kind | activate_standby, scale_out, scale_in, primary_failure, failback, provision_and_deploy, standby_repair, reconcile, apply_policy, create_backup, verify_backup, restore_application, start_load_test, stop_load_test |
| PolicyVersion.status | pending, active, failed, superseded |
| PolicyVersion.activation_phase | validating, compiling, applying, verifying, completed |
| Backup capture_status | capturing, captured, failed |
| Backup verification_status | unverified, verifying, verified, failed |
| Event / audit source | dashboard, automation, reconciliation, alertmanager, timer |
| Relief request status | pending, in_progress, resolved |

Allowed transitions:

| Enum | Transitions |
|---|---|
| Instance lifecycle | reserved to provisioning to deploying to ready; ready to draining to deleting to deleted; any non-deleted state to failed or unknown; unknown to any state once reconciled; failed to deleting |
| Slot state | free to reserved to bound; bound or reserved to releasing (a reserved slot whose resource was never created, or whose creation failed, also takes the releasing step and its absence evidence); releasing to free only with provider-confirmed absence; reserved, bound or releasing to held_uncertain when existence is unknown; held_uncertain to free or bound only by a reconcile operation with evidence |
| Operation status | queued to running; running to waiting, verifying, paused, reconciling or cancelling; waiting, verifying, paused and reconciling back to running; verifying to succeeded; running, waiting, verifying, reconciling to failed; cancelling to cancelled; succeeded, failed and cancelled are terminal |
| PolicyVersion.status | pending to active or failed; active to superseded; failed and superseded are terminal |
| PolicyVersion.activation_phase | validating to compiling to applying to verifying to completed |
| Backup capture_status | capturing to captured or failed (terminal) |
| Backup verification_status | unverified to verifying; verifying to verified or failed; verified or failed to verifying (re-verify) |
| Relief request status | see the table above |

Routing mode changes are driven by apply_routing: none to backup (standby) or active, backup to active, active to draining, draining to none or backup. An instance with routing mode `active` must be lifecycle `ready`.

## Invariants

- One slot per possible VM: one warm slot plus elastic slots per provider. The cap (absolute ceiling 5 per provider, immutable) is enforced on bound/reserved/uncertain slots per provider, and the warm slot counts within its provider's cap (so at most 10 cloud VMs in total, and at most 4 elastic on the warm provider). Exactly one slot is the protected warm standby.
- Pending, uncertain and unknown resources count toward the per-provider cap.
- `interim-warm-<provider>` counting rule: when the warm slot is `held_uncertain`, the interim standby on the other provider uses this separate slot and counts within that provider's cap of 5, while the original warm slot stays counted until reconcile resolves it. After resolution exactly one warm slot remains; the other is drained and released via the scale-in rules.
- Instance/provider-resource IDs are unique within their provider account.
- Partial creation/unknown deletion occupies its slot.
- Slot binding, operation admission, and lock checks use transactions.
- Operations with the same idempotency key and fingerprint return the existing operation.
- Relief request updates succeed only when `expected_version` equals the stored `version`.
- Maintenance controls cannot be overwritten by restoring app.
- Avoid foreign keys from ops into app; use soft business references where needed.
- App runtime roles can read only the minimal maintenance flag plus required app data.
- Backup rows are inserted with capture_status `capturing` at job start; a backup is eligible for restore only when verification_status is `verified`.
- Secrets are not policy fields or API-readable management records.

## Connectivity

Use bounded application pools, initially two connections per app instance. Supabase free-project connection limits must be checked during implementation.

Use the Supabase session pooler (port 5432, IPv4) for all roles: app runtime, management, backup and restore. It supports pg_dump, migration and restore session semantics. Do not use transaction pooling. Avoid named prepared statements in all clients.

Supabase Free projects pause after 7 days of low activity. A keep-alive `SELECT 1` runs every 6 hours, plus a wake check at T-24h and T-1h before the demo. Alert when database size exceeds 400 MB (Free limit 500 MB).

Use separate limited database roles:

- app runtime: required app data plus the minimal maintenance flag read.
- management: ops schema.
- backup: read-only access to schema app for pg_dump.
- restore: separate role that owns schema app; used only by the controlled restore job (schema swap, grants).

Migration/restore credentials are provided only to controlled jobs. With all access through Node.js, public anonymous database API access is not necessary. There is no browser service-role key.
