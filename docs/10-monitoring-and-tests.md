# Monitoring, load generation, and acceptance

## Measurement contract

Prometheus collects host metrics from app VMs and application HTTP metrics; HAProxy contributes backend/traffic metrics. Use a containerized host exporter with the correct host mounts/namespaces, verified against the pinned exporter documentation. A container's CPU view must not be mislabeled as whole-VM CPU.

Sample and evaluate initially every five seconds. Compute CPU utilization from counters over 30 seconds, then apply the configured sustained condition. Record samples/timestamps and show freshness.

Fleet CPU is weighted by vCPU across currently serving ready app instances. Warm idle and supporting VMs are excluded. HAProxy routing state and ops inventory provide membership labels, not arbitrary browser-supplied labels.

Health and routing signals: HAProxy checks only `/health/live` with `inter 10s fall 3 rise 6` on the primary server line (with `timeout check 4s` in `defaults`/`backend`, never on the server line) (policy `health.consecutive_failures` 3, `consecutive_successes` 6, `timeout_seconds` 4); failure detection takes about 30 seconds and HAProxy itself enforces 60 seconds of healthy checks before return. Standbys and elastic servers use rise 2. `/health/ready` (database/schema) feeds the dashboard, Prometheus and StackStorm admission only and never removes a backend, so a Supabase outage does not take backends out.

Staleness is evaluated per serving member, not per fleet. `cdr_member_serving` (1 per member) includes only members currently receiving traffic (routing mode active, weight > 0). Healthy idle backups, such as the warm standby, are excluded from the freshness and staleness rule. Illustrative PromQL (labels verified against the pinned exporter):

```
# serving member whose newest CPU sample is older than 30 s
# (timestamp() on the raw instant-vector selector gives the sample time within lookback)
(time() - timestamp(node_cpu_seconds_total{role="app",mode="idle",cpu="0"}))
  and on(instance) cdr_member_serving == 1
  > 30

# serving member with no CPU series at all (missing member)
cdr_member_serving == 1
  unless on(instance) node_cpu_seconds_total{role="app",mode="idle",cpu="0"}

# serving member whose relief scrape target is not up (job-scoped: another job's healthy scrape of the instance cannot mask it)
cdr_member_serving == 1 unless on(instance) (min by (instance) (up{job="relief"}) == 1)
```

If the CPU series is absent entirely, `absent(...)` style rules also fire, so a vanished series never reads as zero. A member that HAProxy has confirmed down is removed from membership and does not freeze scale-out.

### Alert rules, routing and dashboards (implemented, `drproj-mgmt/monitoring`)

Rules live in `monitoring/rules/cdr.yml`; every rule has a firing and a not-firing promtool case in `monitoring/rules/tests/cdr.test.yml` (`tests/compose/promtool.sh` runs `promtool check config`, `check rules`, `test rules` and `amtool check-config` with the pinned images). The staleness expression above is written with the age comparison parenthesized, `((time() - timestamp(...)) > 30) and on(instance) cdr_member_serving == 1`: as printed, `cdr_member_serving == 1 > 30` binds the comparison first and can never fire.

| Alert | Condition | Severity |
|---|---|---|
| `CdrServingMemberStale` | newest app CPU sample older than 30 s for a serving member (the four serving-member rules carry `cdr_event: metrics.stale`) | critical |
| `CdrServingMemberMissing` | serving member with no app CPU series | critical |
| `CdrServingTargetDown` | serving member whose `up{job="relief"}` (minimum per instance) is not 1 | critical |
| `CdrMembershipAbsent` | `absent(cdr_member_serving)` | critical |
| `CdrCpuHigh` | vCPU-weighted CPU of serving members only above 60% for 30 s; label `cdr_event: app.cpu.high` | warning |
| `CdrCpuLow` | the same expression below 20% for 120 s; `cdr_event: app.cpu.low` | info |
| `CdrPrimaryUnhealthy` | primary (`cdr_member_serving{role="primary"}`) whose relief scrape target is not up for 30 s; `cdr_event: app.primary.unhealthy` (HAProxy health replaces this signal in P6) | critical |
| `CdrBackendCertExpiringSoon` | `probe_ssl_earliest_cert_expiry - time() < 14 * 86400` (no blackbox targets until Phase 5/6) | warning |
| `CdrSupabaseDbSizeHigh` | `cdr_supabase_db_size_bytes > 400 MB` | warning |
| `CdrSupabaseKeepaliveStale` | last successful keep-alive older than 12 h | warning |
| `CdrBackupStale` | latest captured backup older than 26 h | warning |

Labels: every alert carries `severity` and `policy_version`. The latter is a Prometheus `external_labels` entry (static `"1"` until Phase 4 policy activation re-renders it), because PromQL cannot turn the value of `cdr_policy_active_version` into a label. Relief targets (file_sd, `role="app"`) are relabeled so `instance` is `<host>:9100`, matching `cdr_member_serving` and node_exporter, while the relief `instance_id` label is kept.

Routing: one Alertmanager route and one receiver, the StackStorm webhook `http://st2api:9101/v1/webhooks/cdr_alert` with `St2-Api-Key` read from a file; `group_by [alertname, instance]`, `group_wait 0s`, `group_interval 5s`, `repeat_interval 60s` (policy `alerts.*`). The `cdr_alert` rule runs `cdr.alert_intent` (P3.2 ran `cdr.record_alert`, which only recorded). It upserts firing alerts that carry a hex fingerprint and a `policy_version` into `ops.processed_events` (`source=alertmanager`); resolved or malformed alerts are not recorded. It then maps the `cdr_event` label to a capacity intent (docs/07, Resolved engineering defaults P4.3); alerts without a mapped event only record.

Grafana (provisioned from files, anonymous access off): CDR Fleet, CDR Relief HTTP, CDR Management VM, CDR Supabase. Panels use only metrics that exist today; relief panels stay empty (not zero) until application VMs are scraped, and the management dashboard has no whole-VM host metrics because no exporter runs on the management VM yet.

Recovery time targets (targets to measure, not guarantees): HAProxy failover under 40 s (3 x 10 s checks plus reload); overload relief via warm standby under 90 s; one elastic VM ready under 8 minutes (480 s readiness deadline); with one retry under 17 minutes.

Alerts beyond scaling signals:
- Backend certificate expiry: blackbox_exporter `probe_ssl_earliest_cert_expiry - time() < 14 * 86400`.
- Supabase database size above 400 MB (Free limit 500 MB; restore preflight requires `current_db_size + restored_app_size * 1.2` under 450 MB).
- Supabase keep-alive: `SELECT 1` runs every 6 hours; alert when the last successful keep-alive is older than 12 hours. A pre-demo wake check runs at T-24h and T-1h.

Metrics:
- CPU/memory per VM, process/container resource use where needed.
- Request rate, active/in-flight requests, latency distributions, 4xx/5xx and transport errors.
- HAProxy backend health, routing mode, weights, connection counts.
- Provisioning/deployment/readiness/drain/delete duration.
- Policy version, last successful reconciliation, interrupted operation count.
- Backup age, job failures, verification and restore duration.
- Metrics source age and health.

On stale metrics (>30 seconds by default) for any serving member, a missing serving member, or absent membership, freeze CPU-based scale actions and open an incident. Do not interpret missing values as zero. HAProxy failure routing continues. Management VM memory (4 GB) is measured with per-container limits, one heavy job at a time, and a 2 GB swapfile. T18 preview (local Docker Desktop, emulated amd64 StackStorm, not the management VM; `drproj-mgmt/tests/compose/t16-t30.local.out`): the full stack idled at about 1.9 GB summed over per-container peaks and about 2.0 GB under one `pg_restore` of the seed dump (a 10 KB dump, so this exercises the harness, not the T18 result). The first-draft st2 ceilings (64-128 MB) were SIGKILLed at startup and the sum of always-on `mem_limit` ceilings is now about 4.0 GB, so T18 on the VM must confirm or trim them. On a fresh MongoDB, start `st2api` first and wait for it to be healthy before the other st2 services (each registers the runner types and all but one hit a duplicate-key error, then restart).

## Bounded k6 tests

The dedicated testing VM runs k6 against https://app.cproj.obed.one. Prometheus may observe the testing host for generator diagnostics but excludes it from application scaling.

Default: arrival-rate scenario, one application request per iteration for an explicitly controlled RPS, configurable bounded rate/duration, manual start/stop, maximum ten minutes initially.

Profiles:
1. Smoke: create/read/status-update with correct idempotency handling.
2. Baseline: ordinary request traffic and measured CPU/latency.
3. Compute pressure: demo report endpoint doing calibrated bounded CPU work.
4. Sustained surge: hold offered RPS constant through capacity activation/provisioning.
5. Recovery: reduce/stop load and watch cooldown/scale-in.

If a request consumes approximately 100 ms of CPU, roughly ten completed requests per second can consume a core, excluding overhead. Calibrate this; it is not a benchmark promise. Do not force memory exhaustion to demonstrate scaling.

Keep health checks responsive using bounded worker-thread/worker-pool processing. Do not multiply CPU work by unbounded caller parameters. Run constant offered load after scaling to demonstrate actual latency/utilization improvement. Capture k6 achieved rate and dropped iterations; insufficient generator capacity must not look like server improvement.

## Acceptance matrix

| ID | Scenario | Required evidence |
|---|---|---|
| T01 | Baseline | Primary serves requests; exactly one warm standby healthy on the warm provider (DigitalOcean by default); no elastic VMs |
| T02 | Idempotent submission | Parallel same-key identical requests create one row; changed body conflicts |
| T03 | Overflow | Above-60%/30-s event; warm standby activated before any VM creation; relief under 90 s |
| T04 | Continued surge | After 60-s cooldown and CPU re-evaluation, one elastic VM per step, alternating DO/Linode; deploy/readiness/routing; per-provider cap of 5 never exceeded |
| T05 | Primary failure | Three failed checks (about 30 s); primary set to maint; warm standby becomes active, then exactly one elastic redundancy VM is provisioned on the non-warm provider, exactly once; any further VMs come only from CPU scale-out. Assert: a low-load primary failure never provisions more than one elastic VM |
| T06 | Primary return | Recovered primary stays in maint; 60 s of continuous direct management probes (`/health/live`, `/health/ready`), then weight 0 is set before `state ready`; HAProxy UP (rise 6) before ramp; ramp 10%/50%/100% with 60/60/120-s steps and gates; warm returns to backup; elastic removal obeys scale-in |
| T07 | Low load | Below-20%/120-s decision; one elastic VM removed per step, newest first, never the warm standby; safe drain; exact VM deletion; slot freed only after provider confirmation |
| T08 | Slow/hung provisioning | Checkpoints at 240/360/480 s; verified cleanup; one retry; alternate provider; eventual stop/incident |
| T09 | Ambiguous provider timeout | Resource remains counted; no blind create/fallback; reconciliation evidence |
| T10 | Duplicate alerts/concurrency | Simultaneous failure/load/reconciliation events yield a single slot owner |
| T11 | Policy edit | Version validation, applied config evidence, failed activation rollback, obsolete alert suppression |
| T12 | Daily/immediate dump | Success/failure catalogue, checksum, actual artifact, correct app-only scope |
| T13 | Deleted data | Isolated verification, maintenance, safety dump, restored seeded records, ops history preserved |
| T14 | Restore failure | Maintenance retained; verified safety rollback or explicit unresolved incident |
| T15 | Management restart | Existing HAProxy service continues; interrupted intent reconciled without duplicates |
| T16 | Metrics/Supabase outage | Stale/dependency incidents; `/health/live` checks keep backends in; no speculative scale-in; no cascading restarts |
| T17 | DNS/TLS | App goes to LB; management goes directly to management; renewal hook validated; cert-expiry alert fires under 14 days |
| T18 | Resource baseline | Full management stack plus one heavy job fits measured 8-vCPU/16-GB limits or clearly reports constraint |
| T19 | Maintenance via direct IP | Write to a VM public IP during maintenance returns 503 `MAINTENANCE_WRITES_BLOCKED` with `Retry-After: 60` and `Cache-Control: no-store`; unreadable flag fails closed with `DEPENDENCY_UNAVAILABLE` |
| T20 | Backup expiry/retry/no-overlap | Retention computed from capture finish; one retry on failure; overlapping runs rejected; `expired_at` set |
| T21 | Drain timeout | Drain exceeding its limit pauses the operation with `infrastructure.drain.timeout`; no deletion until resolved |
| T22 | No scale-in during primary outage | Low-load decision during primary outage is refused; failure-triggered intent preempts queued scale-in |
| T23 | Failback reverse-on-regression | Injected 5xx/p95/CPU regression at a ramp step reverses to the previous step; gate evidence recorded |
| T24 | k6 bound enforcement | Rate, duration (max ten minutes) and manual stop limits enforced; out-of-bound requests rejected |
| T25 | Request list/filter/404 | Relief request list pagination and filters work; unknown ID returns 404 problem response; invalid transition returns 422 |
| T26 | Overload-only recovery | After elastic removal with stable primary, warm standby returns to backup via mode=normal, not stopped or deleted; 60-s observation; reverts to overflow on regression within 120 s |
| T27 | Warm standby repair | Unhealthy or mismatched warm standby goes through maint and classification (redeploy, power on, reprovision into the same slot, or hold slot on unknown API); interim warm on the other provider if the warm provider is unavailable; done only after ready, digest match and HAProxy UP as backup |
| T28 | Scale-out across providers | Elastic VMs added one per step, alternating DO/Linode, skipping a provider at cap or unavailable; cooldown between steps |
| T29 | Cap enforcement | Maximum 5 VMs per provider including pending/uncertain resources; sixth request refused with evidence; schema rejects limits above 5 |
| T30 | Supabase keep-alive/pause | 6-hour `SELECT 1` keep-alive runs; stale keep-alive alert; pre-demo wake check at T-24h/T-1h; size alert above 400 MB |
| T31 | Management basic-auth | POST/PATCH/PUT/DELETE on ops host without credentials return 401; GETs stay open; audit actor `operator` |
| T32 | Rate limit | HAProxy stick-table limits return 429 with `Retry-After` (general, POST, demo workload); 8 KB body limit and quantity maximum enforced |
| T33 | k6 test-host exemption | Requests from the testing host IP above the public limits are not throttled (no 429) while other IPs get 429; k6 caps (200 rps, 600 s) and the bounded worker pool still apply; app per-IP limit also skipped for that IP |
| T34 | Automatic backup verification | After capture, verification runs unattended (no keys or operator input) in the isolated restore-test container with the same-major pg tools; `verification_status` reaches `verified`; size of restored app schema recorded |
| T35 | Restore preflight size headroom | Restore is rejected (422) when `current_db_size + restored_app_size * 1.2` is not under 450 MB; accepted with sufficient headroom; no maintenance or schema change on rejection |
| T36 | Interim warm slot counting | Warm slot `held_uncertain` with the warm provider unknown: interim standby uses slot `interim-warm-<provider>` on the other provider, both slots count toward their provider caps (5) until reconcile resolves; afterwards exactly one warm slot remains and the other is drained and released |

Tests against real provider resources are run only when the actual deployment/testing request authorizes them. Local adapters contract-test behavior; label them as simulations. Inject provider failures through controlled adapter responses or targeted app-instance failure, not a claim to have caused a real provider-wide outage.

## Evidence

Store test IDs, input workload, start/end timestamps, deployment/config/image versions, StackStorm references, provider IDs, metrics snapshots, k6 summaries, row-integrity results, and observed failures. Never report an unexecuted acceptance row as passed.
