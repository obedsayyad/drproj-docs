# Editable policies and defaults

config/policy-defaults.json is the seed specification. A validated active policy in Supabase is authoritative after initialization. Do not hardcode dashboard knobs into workflow source.

## Confirmed defaults

| Policy | Value |
|---|---|
| Primary traffic | Self-hosted first |
| Warm provider | DigitalOcean (`capacity.warm_provider`; Linode allowed as interim if DigitalOcean is unavailable) |
| Warm application count | 1 (on either provider) |
| Elastic provider order | Alternate DigitalOcean/Linode (`elastic_provider_strategy: alternate`), skipping a provider at cap or unavailable |
| Cloud application cap | 5 DigitalOcean + 5 Linode (`capacity.max_cloud_vms_per_provider`, each 0..5) |
| Scale step | 1 VM per step (out and in), then cooldown and re-evaluate |
| Scale-out CPU | Above 60% |
| Scale-out sustained period | 30 seconds |
| Scale-in CPU | Below 20% |
| Scale-in sustained period | 120 seconds |
| Scale-out cooldown | 60 seconds after readiness/routing activation of each single-VM step |
| HAProxy health interval | 10 seconds |
| Health check | `/health/live` only, 4 second timeout (`health.timeout_seconds`) |
| Unhealthy threshold | 3 consecutive failed checks (detection about 30 s) |
| Failback observation | 60 continuous seconds healthy by direct probes while in maint (HAProxy checks are off), weight 0 set before `ready`, then capacity-tested gradual return |
| Primary-failure response | Activate the warm standby; ensure exactly one elastic redundancy VM on the non-warm provider (or the other if at cap or unavailable) that shares traffic when ready; further VMs only via normal CPU scale-out (1 per step, cooldown, re-evaluation) |
| Initial readiness checkpoint | 240 seconds |
| Further readiness checkpoints | 360 and 480 seconds |
| Hard readiness deadline | 480 seconds |
| Provisioning retries | 1 per provider after confirmed cleanup |
| Both providers exhausted | Stop provisioning; raise incident |
| Backup | Daily at 00:00 UTC |
| Backup retention | 30 days |
| Backup scope | Application schema/tables and their data |
| Backup verification | Automatic after capture (`backup.verify`, timeout 900 s) |
| Restore | Operator-started from a verified backup no older than 7 days (`recovery.require_verified_backup`, `max_verification_age_days`) |
| Maintenance write rejection | 503, `Retry-After: 60` (`maintenance.retry_after_seconds`) |
| Supabase keep-alive | `SELECT 1` every 6 hours (`runtime.supabase_keepalive_interval_hours`) |

A VM ready before the first checkpoint should be admitted immediately. Checkpoints are maximum waiting/status milestones, not artificial delays. Provisioning deadline covers creation, bootstrap, deployment, and readiness for each attempt. Terraform command success is not readiness.

## Sensible engineering defaults

| Policy | Initial value |
|---|---|
| CPU aggregation | vCPU-weighted active fleet average |
| Metrics scrape/evaluation | 5 seconds / 5 seconds |
| CPU rate calculation window | 30 seconds |
| Stale metric threshold | 30 seconds |
| HAProxy recovery threshold | 6 consecutive successful checks on the primary (60 s healthy); 2 on standby/elastic |
| Reconciliation | Every 60 seconds; also after management restart |
| Drain deadline | 60 seconds; pause deletion and raise incident |
| Alert routing group wait | 0 seconds for automation alerts |
| Alert routing group interval | 5 seconds |
| Alert repeat interval | 60 seconds; duplicate notifications do not duplicate operations |
| Capacity workflow concurrency | 1 per protected application |
| Failback steps | 10%, 50%, 100% primary traffic targets |
| Failback step observation | 60 seconds for the 10% and 50% steps, 120 seconds for the 100% step (`step_observation_seconds`, `final_step_observation_seconds`) |
| Failback ramp weights and baseline | During the ramp the cloud serving lines run at weight 256 (HAProxy's maximum, set before the ramp starts) and the primary's weight for a target share of `s`% is `round(256·s/(100−s))`: 28 for 10%, 256 for 50% (equal weights); the 100% step returns to normal mode (cloud lines back to `warm_act` maint / elastic as scale-in decides) and the primary at its full weight. The baseline for the p95 gate is the median of the application p95 (`relief_http_request_duration_seconds`, 1 m windows) over the 10 minutes before the failure, stored in the operation when the failure is recorded |
| Failback step gates | 2 consecutive health successes, samples timestamped after step start, 5xx under 1%, p95 under 1.5x baseline, primary CPU under 50%; any gate failure reverses to the previous step |
| Capacity lease | 120 seconds, renewed every 20 seconds from a separate heartbeat; after 2 missed renewals the worker starts no new side effects |
| Notifications | Dashboard only |
| Monitoring retention | 3 days initially, resource-tested and editable |
| Backup overlap | Disallowed |
| Missed daily backup | Retry once after 60 seconds, then raise incident |
| Restore failure | Remain in maintenance; attempt the pre-restore safety dump rollback once |
| Demonstration load | Manual start; fixed-duration bounded rate; default 10 minutes maximum |
| Rate limits | Public per-IP HAProxy limits (429); the k6 test host IP is exempt from HAProxy and app per-IP limits, while k6's own caps (200 rps, 600 s) and the app's bounded worker pool still apply |
| Individual app DB pool | Maximum 2 connections initially; validate total against actual Supabase limits |

RTO targets (to be measured, not guaranteed): HAProxy failover under 40 s; overload relief via the warm standby under 90 s; one elastic VM ready under 8 minutes; with one retry under 17 minutes.

The 30-second CPU calculation window plus sustained-alert evaluation adds observation time. Never advertise exact 30-second end-to-end scaling. HTTP error failover may also depend on in-flight requests and retry rules.

## CPU semantics

Only traffic-serving, ready application backends contribute to active-fleet CPU. Idle standby and management/LB/testing machines are excluded.

Stale metrics: freeze CPU-based scaling only if a serving member is stale or missing, or the membership list is absent. A member that HAProxy has confirmed down is removed from the membership and does not freeze scale-out. Stale metrics are never treated as zero load. With equal one-core app VMs, a simple average is equivalent to vCPU weighting.

Before removal, project demand onto the remaining eligible capacity. Permit scale-in only when projected remaining utilization stays safely below the scale-out threshold, dependencies are healthy, primary recovery is stable, and redundancy requirements remain satisfied.

CPU is the initial scaling signal. Latency/errors and database health are explanatory/gating signals. Extra app machines cannot repair Supabase overload. Memory saturation raises an incident; do not automatically scale on arbitrary OR combinations of unhealthy dependencies.

## Policy activation

1. Validate typed input, ranges, dependencies, and expected current version.
2. Persist a pending version and audit event.
3. Compile affected Prometheus rules, HAProxy configuration, StackStorm timer/action parameters.
4. Validate generated files; persist configuration artifacts.
5. Apply and reload affected services; verify actual loaded configuration.
6. Activate the version once the required changes succeeded.
7. On failure, restore/reinstate the old configuration, mark failure, and keep the old active version.

Generated alerts contain policy version. Ignore inactive/obsolete notifications as action requests; preserve them as history if useful. Recheck current metrics before acting. Repeated events during a running operation are coalesced.

Running operations capture a policy version. Lowering a VM limit below occupied capacity is a pending transition, not permission to force-delete resources. Do not silently reinterpret in-progress work.

Implemented in P4.4 (local simulation, docs/07 "Resolved engineering defaults (P4.4)"): steps 1-6 for the validated settings and the Prometheus `policy_version` label; the exclusive lease only when a capacity limit changes; a cap lowered below the occupied count activates and reports `pending_transition` per provider (nothing is deleted, new reservations above the cap are refused, scale-in and reconcile converge); a change whose compile target (HAProxy, k6, Prometheus rule thresholds, StackStorm timers) is a later phase fails `not_implemented` and keeps the old version active.

Cron times use UTC. Editing backup frequency changes the StackStorm schedule; editing retention changes cleanup eligibility. Managed Supabase plan settings are not part of this system because pg_dump is selected.

## Validation

CPU scale-in must be lower than scale-out. Durations must be positive. Readiness checkpoints must increase and the last must equal the deadline (240/360/480 by default). Each per-provider cloud cap (`capacity.max_cloud_vms_per_provider.digitalocean` and `.linode`) must be an integer 0..5; values above 5 are rejected (immutable ceiling). Warm slot floor: the cap of `warm_provider` must be at least 1 so the warm standby always has a slot; setting it to 0 is rejected (the interim warm on the other provider is a runtime fallback, not a configuration). `warm_instances` is fixed at 1. `elastic_provider_strategy` must be a supported value (`alternate`). `step_vms` must be 1. Reducing a cap below the actual reserved count (including pending and uncertain resources) waits for safe convergence. Failback weights must increase to 100; `step_observation_seconds` and `final_step_observation_seconds` must be positive. Health thresholds must be 1..10 and `timeout_seconds` must be less than `interval_seconds`. `lease_renewal_seconds` must be well under `lease_seconds` (at most one third). `backup.verify.timeout_seconds` must be positive; `recovery.max_verification_age_days` must be at least 1; `maintenance.retry_after_seconds` must be positive. Enforce bounds on workload duration/rate, SSH targets, provider IDs, image references, and storage paths.
