# Operational workflows

All mutations use stable operation IDs and per-attempt IDs. Obtain a fenced capacity lease, recheck current desired state, and reserve a slot before creating resources. See docs/07-automation.md for state boundaries.

## Normal state

Primary serves all relief traffic. One warm standby (on `capacity.warm_provider`, default DigitalOcean) runs the same pinned image, passes readiness checks, and is a HAProxy `backup` server. No elastic VMs exist. Up to 5 DigitalOcean and 5 Linode cloud VMs are allowed in total (`capacity.max_cloud_vms_per_provider`); the warm standby counts toward its provider's cap. HAProxy checks only `/health/live`; `/health/ready` never removes a backend.

## Scale-out

1. Receive the above-60%-for-30-seconds candidate alert.
2. Check fresh metrics, dependency health, policy version, cooldown, maintenance, and in-progress work. Freeze CPU scaling only if a serving member's metrics are stale or missing, or membership is absent; a member HAProxy has confirmed down is removed from membership and does not freeze scale-out.
3. If the warm standby is healthy but idle, activate it (routing mode `active`) before creating another VM.
4. Apply/persist HAProxy configuration; verify requests reach the newly active backend.
5. Observe a 60-second cooldown and reevaluate current sustained demand.
6. If demand remains high, choose the provider: alternate DigitalOcean/Linode (`capacity.elastic_provider_strategy: alternate`), skipping a provider that is at its cap (pending and uncertain resources count) or unavailable. If no provider qualifies, stop and raise an incident.
7. Atomically reserve one elastic slot on that provider. Terraform provisions that slot in its own state; Ansible configures Docker and deploys the pinned app.
8. Check readiness, connectivity, instance identity, deployed digest, and schema compatibility.
9. Admit early if ready; otherwise report 240-, 360-, and 480-second checkpoints, failing at 480 seconds.
10. Add the ready backend, verify routing, update observed state, and record elapsed time.
11. Add exactly 1 VM per step. After the 60-second cooldown, re-evaluate CPU before adding another, up to the per-provider caps.

## Primary failure

1. HAProxy checks the primary's `/health/live` every 10 seconds; three consecutive failures (`inter 10s fall 3`, detection about 30 seconds) mark it unhealthy.
2. `apply_routing` puts the primary in maintenance (`set server be_app/primary state maint` via the Runtime API) so a recovered primary stays out until failback. The warm standby becomes `active`. State is persisted with the server-state file and `show servers state` is dumped after each runtime change and before reload.
3. StackStorm receives the incident and records actual observations.
4. The warm standby becomes `active`, and exactly ONE elastic redundancy VM is ensured, on the provider that is not the warm provider (falling back to the other provider if that one is at its cap or unavailable). Any further VMs come only from the normal CPU scale-out rules: 1 per step, 60-second cooldown, re-evaluate CPU. A low-load primary failure therefore never provisions the whole fleet.
5. If an elastic VM already exists or is being created (any reserved or bound elastic slot, whichever operation holds it), reuse/await it rather than duplicate it: no second VM is reserved. A resumed run continues in the slot it already holds.
6. Once ready, the warm standby and elastic VMs share traffic.
7. Continue checking the primary and show the incident/recovery timeline.

No database restoration is triggered solely by a VM failure. HAProxy failover target is under 40 seconds (3 x 10s checks plus reload).

## Automatic failback

HAProxy `maint` disables health checks on that server, so HAProxy cannot observe the primary's recovery while it is in maintenance. StackStorm observes it directly.

1. While the primary is in `maint`, StackStorm probes it directly (management VM to primary port 8443, `/health/live` and `/health/ready`) and requires 60 continuous seconds of healthy probes.
2. Before leaving `maint`, set `set weight be_app/primary 0` and switch to ramp mode (cloud servers non-backup). Weight is set before any traffic is admitted.
3. Set the primary to `ready` (`set server be_app/primary state ready`). HAProxy health checks resume; at weight 0 the primary receives no traffic. Wait for HAProxy to report it UP (`rise 6`, about 60 seconds).
4. Ramp the primary's weight to 10%, 50%, then 100% of its share (weights and the p95 baseline: docs/03), observing 60s, 60s, and 120s respectively. An idle healthy primary alone does not prove spare capacity. Gate each step on: 2 consecutive health successes, samples timestamped after the step started, 5xx rate below 1%, p95 latency below 1.5x baseline, and primary CPU below 50%. Any gate failure reverses to the previous step (and ultimately to healthy cloud backends).
5. Once the primary carries the workload stably, return to normal mode (warm standby back to `backup`).
6. Retain elastic VMs until scale-in rules and remaining-capacity checks allow safe removal.
7. Resolve the incident only after service verification, not merely primary boot.

## Scale-in

1. Active-fleet CPU remains below 20% for two minutes.
2. Confirm primary is serving stably, dependencies are healthy, no primary outage is in progress, and remaining capacity is sufficient. A primary outage is an open `primary.unhealthy:*` incident or a non-terminal `primary_failure` or `failback` operation; the scale-in then ends as a no-op with `noop_reason: primary_outage` and starts nothing. Remaining capacity means a ready, bound warm-role standby exists. A failure-triggered intent preempts a queued scale-in (it becomes a succeeded no-op `superseded`).
3. Protect the fixed primary and the warm standby slot (including an `interim-warm-<provider>` slot while it is the active standby); the warm standby is never a scale-in candidate.
4. Remove 1 elastic VM per step, the newest elastic VM first.
5. Mark it draining; HAProxy stops assigning new requests to it.
6. Observe in-flight connections. At the 60-second drain deadline (`scale_in.drain_timeout_seconds`), pause deletion and raise incident if still busy: the server returns to `active`, the incident `infrastructure.drain.timeout:<slot>` opens, the operation becomes `paused` (not terminal) and the lease is released; nothing is deleted.
7. Persist routing removal and verify it.
8. Terraform removes the specific resource in that slot's own managed state.
9. Query provider existence. Release the slot only on confirmed deletion.
10. Keep deletion-pending/unknown resources counted until reconciliation succeeds.
11. After a step, observe and re-evaluate before removing the next elastic VM.

Do not remove cloud redundancy during primary outage solely because traffic is low.

### Overload-only recovery

When the overload has ended, all elastic VMs are removed, and the primary is stable, return the warm standby to `backup` (standby) through `apply_routing` with mode=normal. Do not stop or delete it. Observe 60 seconds; revert to overflow (warm `active`) on regression within 120 seconds.

## Warm standby repair

Trigger: `standby.unhealthy` or a reconciliation mismatch on the warm slot.

1. Take the capacity lock.
2. Recheck (maintenance mode ends the run as a no-op) and classify the warm slot first, then put the warm server in maintenance in HAProxy (slot routing `none`; only when there is something to repair).
3. Classify using the provider API, tags, and Terraform state for the warm slot:
   - (a) VM present but app broken: redeploy the pinned app.
   - (b) VM powered off: power on, then redeploy.
   - (c) Absence confirmed: clean up leftovers and provision into the same warm slot.
   - (d) Provider API result unknown: hold the slot, raise an incident, and retry reconciliation with backoff. Do not provision.
4. If the warm provider is unavailable (the warm slot is `held_uncertain`), provision an interim warm standby on the other provider in its own slot, `interim-warm-<provider>`. It counts within that provider's cap of 5, and the original warm slot stays counted until reconcile resolves it. After resolution exactly one warm slot remains; the other is drained and released through the scale-in rules.
5. Allow 1 retry for a failed repair.
6. Done only after `/health/ready` passes, the deployed digest matches, and HAProxy shows the server UP as `backup`.

### As implemented (P4.4, local simulation)

`cdr.standby_repair` (`actions/workflows/standby_repair.yaml`). Routing, the provider answers and the Prometheus reload are simulated; HAProxy and the real adapter are P5/P6. Every step runs under the lease and asserts it right before the provider call.

- **Classification** (`cdr.standby_classify`, read-only; the answer comes from the slot table, the provider API `get` plus the project-tag listing for slot `warm`, and the readiness check, never from a timeout): `healthy` (present, on, ready with the pinned digest: only the record was stale), `app_broken` (present, on, not ready), `powered_off`, `absent` (slot free, or `get` says absent, the listing is `ok` and no other resource carries the slot's tag; the absence evidence is returned), `held_uncertain`, `unknown` (the provider did not answer, or a tagged resource nothing explains claims the slot: nothing is created, adopted or deleted by tag alone), `busy` (reserved or releasing under another operation) and `maintenance`.
- **(a)/(b)** `provider_power_on` (b only), a fresh readiness window from the captured policy (`standby_window`, 240/360/480 by default), `run_heavy` deploy, `wait_ready`; a missed deadline is tried once more (the one retry), then the incident `standby.repair_failed:<slot>` and the operation fails `repair_failed`; the slot is not released. A readiness check that never gets an answer holds the slot as in (d).
- **(c)** `begin_slot_release`, `release_slot` with the classification's absence evidence, `reserve_slot` kind `warm` and `provision_and_deploy` with `slot_kind: warm`: the standby retries on the same provider only (it never alternates), then raises `provision.exhausted`. A free warm slot skips the release.
- **(d)** `mark_slot_uncertain` (still counted), incident `standby.uncertain:<slot>` (critical), then the interim branch; nothing is created on the warm provider. The operation ends `failed` with `outcome.error: held_uncertain` when no interim warm standby can be reserved; reconcile is the backoff (below).
- **Interim warm.** Only while the warm slot is `held_uncertain` and the OTHER provider answers (`observe` is `available`) and no interim slot is bound yet: `reserve_slot` kind `interim_warm` (the database enforces the cap, the held warm slot and the interim slot both count), `provision_and_deploy` with `slot_kind: interim_warm` into `interim-warm-<provider>`, routing `backup`. If the other provider does not answer either, nothing is created anywhere.
- **Done** = `standby_verify`: readiness check passes, the instance record and the check carry the pinned digest, and routing shows the server `backup` (database projection and the simulation's routing map; the real HAProxy view is P6). Then `standby.repaired`, then `operation.succeeded` with `outcome.path` = `healthy | redeploy | power_on_redeploy | reprovision | interim`.
- **Triggers.** The real `standby.unhealthy` producer (HAProxy health of the standby server) is P6. In P4 the simulation action `cdr.standby_unhealthy` (refuses unless the adapter is the simulation) can first break the simulated standby (`app_broken | powered_off | absent`), opens the operation (`source: automation`) and dispatches it. Reconcile opens `standby_repair` itself (`source: reconciliation`, fingerprint `reconcile.warm_mismatch:<slot>:<generation>`) when the warm slot is `bound` with an instance `failed` or `unknown`, or still `held_uncertain` with no interim warm bound; a free warm slot is not a mismatch (the first standby comes from deployment). At most one in flight, and no new one within twice `lease_seconds` (240 s by default) of the previous one for the same fingerprint (the backoff).
- **After resolution.** Reconcile resolves the held warm slot from provider evidence (`bound`, instance `failed`); the repair finds it `healthy` (or redeploys it); once the warm slot is `bound` and `ready` while an interim slot is still bound, reconcile opens a `scale_in` (fingerprint `reconcile.surplus_warm:<interim slot>`). `ops.scale_in_candidate()` returns that surplus interim slot (and only then; otherwise the newest elastic slot), `begin_slot_release` allows a `scale_in` to release a warm-role slot only while another warm-role slot is `bound`, and `release_slot` needs the provider-API absence evidence. Exactly one warm-role slot stays bound.

## Provisioning failure, retry, and fallback

A failed attempt is not just a failed CLI exit. Query Terraform state and provider APIs, matching project, slot, operation, and resource IDs/tags.

The hard readiness deadline is 480 seconds per attempt, with checkpoints at 240, 360, and 480.

If the resource exists but deployment failed, reconcile/redeploy it when appropriate. If the attempt is unusable, remove it safely and verify absence. Then retry once on the same provider. After exhausted confirmed attempts, try the alternate provider if it is under its own cap.

The slot cap is per provider (up to 5 each). Pending and uncertain resources count toward the cap.

If provider APIs time out and existence cannot be established, hold the slot and stop unsafe fallback. Raise an incident. The provider cap takes precedence over creating a potentially extra unknown VM. Do not free capacity based only on an elapsed timeout.

Once both providers exhaust allowed attempts, stop further automatic provisioning. A new operator action or policy-governed incident reset may explicitly start another operation; reconciliation still checks existing resources but does not silently restart the exhausted provisioning loop.

## Reconciliation

Run every minute and after restart. Compare Supabase desired/reserved state, StackStorm execution, Terraform ownership, provider existence, image/readiness, and HAProxy routing.

- Adopt a still-running StackStorm execution with the same operation_id instead of starting another (adopted when its lease is alive, or it is young and has not taken the lease yet). Otherwise run a provider-tag scan before any retry; an interrupted operation that holds a slot is re-dispatched under the same operation_id and adopts its recorded attempt by tags instead of creating again.
- Resume/reconcile known incomplete work before new creation.
- Discover tagged resources (`project=cdr`, `slot`, `op`, `attempt`) created before a process crash.
- An unknown tagged resource raises an incident (`orphan:<provider>:<id>`) and, when its slot tag names a free slot of that provider, holds that slot `held_uncertain` (counted); never delete by tag alone. Follow the "Adopt tagged orphans" runbook in docs/15-runbooks.md.
- Import/reconcile ownership with appropriate verification when state persistence was interrupted.
- Remove stale routing only for confirmed changes; distinguish unreachable from absent.
- Release slots only after confirmed resource deletion.
- Identify unmanaged/unexpected resources as incidents; do not blindly delete by a broad provider tag search.
- Recheck intent after acquiring the lock; stale queued scaling jobs may become no-ops.
- Use one clear incident for a recurring unresolved condition rather than incident spam.

## Management/control dependency failure

HAProxy health-based routing continues independently. Pause new capacity mutations if Supabase control-state reservations/leases cannot be safely used. Capture unavailable-control-state incidents in StackStorm's native history and reconcile them to ops records when connectivity returns.

The management VM is assumed always available (accepted assumption; see docs/12-boundaries-and-deployment-inputs.md).

Missing CPU metrics from a serving member pause CPU-based scale actions; health failover remains independent. StackStorm restart does not authorize rerunning side effects without reconciliation.

## Policy changes

Use the pending, then activation phases (validating, compiling, applying, verifying), then active or failed process in docs/03-policies.md. Show activation state in the dashboard. Failed config reloads do not authorize the pending policy. Do not apply a policy change as an arbitrary user-provided shell command.

## Backup/restore

Use the detailed workflow in docs/09-backup-and-restore.md. Restore is operator-started, app-scoped, verified in isolation, then performed under global app maintenance with a fresh safety dump and rollback path.
