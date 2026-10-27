# Requirements and accepted decisions

## Project goal

Build a cloud-based disaster relief resource allocation and coordination demonstration. A small emergency-request portal is the protected workload. The platform demonstrates traffic distribution, compute allocation, VM lifecycle automation, recoverable application data, and coordinated recovery.

The demonstrated disaster is a digital infrastructure/application failure. A physical disaster motivates the sample workload; the system does not claim to allocate physical relief inventory optimally.

## Functional requirements

- Create, list, filter, view, and update relief requests.
- Handle food, water, medicine, and shelter categories; positive quantity; location; urgency; Pending/In Progress/Resolved states.
- No user accounts, RBAC or login in the relief application or custom operations dashboard. Dashboard views are public; every mutating ops method (POST/PATCH/PUT/DELETE) requires the shared Caddy Basic Auth password and returns 401 without it (T31).
- Keep app replicas stateless, sharing Supabase.
- Display real instance health, traffic, CPU/memory, provider allocations, workflow steps, backups, incidents, and timestamps.
- Edit policies from the custom dashboard, with validation, version history, and visible activation status.
- Start bounded k6 tests and operator backup/restore operations.
- Use a fixed primary self-hosted VM first.
- Keep exactly one cloud application VM deployed and ready as a warm standby, on DigitalOcean or Linode (default DigitalOcean).
- Activate that standby for overflow and failure.
- Add elastic cloud VMs one per scale step, alternating DigitalOcean and Linode, up to 5 per provider (10 total).
- Provision and deploy using Terraform and Ansible under StackStorm.
- Route only ready app instances through HAProxy.
- Recover failed primary traffic and return it automatically after stable health/capacity verification.
- Create daily application pg_dump files and retain them for 30 days.
- Recover selected application data from a chosen dump through an isolated verification stage.

## Explicit decisions

| Topic | Decision |
|---|---|
| Project time | No time-based design reduction |
| Backend | Node.js |
| Database | Supabase Free PostgreSQL |
| Cloud providers | DigitalOcean and Linode |
| Fixed machines | Primary, management, HAProxy, testing: all self-hosted |
| Public addressing | All VMs have public IPs |
| Infrastructure controller | StackStorm |
| VM provisioning | Terraform |
| VM configuration/deployment | Ansible |
| Containers | Docker/Compose, including management and load-balancer services |
| Public traffic routing | Dedicated single HAProxy VM, application traffic only |
| Monitoring | Prometheus, Alertmanager, Grafana |
| Load generation | Dedicated k6 VM |
| Custom authentication | None, on both custom interfaces |
| Firewall automation | Excluded |
| VPN | Not needed in the selected public-IP design |
| CI/registry | GitHub Actions and GHCR |
| Domain | app.cproj.obed.one and management subdomains |
| DNS provider | Vercel DNS |
| Managed database backups | Do not depend on paid Supabase backups/PITR |
| Backup mechanism | pg_dump / pg_restore only; application schema |
| Backup schedule | Daily 00:00 UTC (05:30 IST) |
| Backup retention | 30 days |
| Backup storage | Persistent management-VM storage |
| Restore trigger | Operator selects a backup and starts recovery |
| Provider exhaustion | Stop automatic provisioning and raise an incident |
| Scaling authorization | Automatic within limits |
| Failback authorization | Automatic after stable observation |

## Exact resource limits

Maximum 5 DigitalOcean plus 5 Linode cloud application VMs (10 total), including the warm standby, starting, draining, and deletion-pending instances (`capacity.max_cloud_vms_per_provider`, each 0..5; 5 per provider is an immutable ceiling). The primary is not a cloud capacity slot. Self-hosted supporting VMs do not consume this limit.

Exactly one warm standby is kept, on DigitalOcean by default (`capacity.warm_provider`); it counts within that provider's cap. If the warm provider is unavailable, the other provider may host an interim warm standby, still counted within its own cap. Elastic VMs are added one per scale step, alternating DigitalOcean and Linode (`capacity.elastic_provider_strategy: alternate`) and skipping a provider that is at its cap or unavailable. Scale-in removes one elastic VM per step, newest first. Each possible VM has its own slot (a warm slot plus elastic slots per provider) with its own Terraform state. Alternate-provider provisioning after confirmed failed-attempt cleanup is permitted within the per-provider caps. When an existing provider resource cannot be accounted for, do not assume spare capacity exists; pending and uncertain resources count toward the cap.

## Engineering defaults delegated to the implementer

The user requested sensible defaults for routine choices. These defaults are editable/reviewable rather than more questionnaire items:
- TypeScript, React/Vite, Express, bounded node-postgres pools.
- Reconciliation every 60 seconds.
- Health checks: interval 10 s, 3 failures to mark down, 6 successes to mark up, 4 s timeout, on `/health/live` only.
- Capacity lease 120 s, renewed every 20 s.
- Provisioning readiness deadline 480 s with checkpoints at 240, 360 and 480 s.
- Scale-out cooldown 60 s after each single-VM step.
- Connection-drain timeout 60 seconds; timeout pauses deletion.
- Incident notifications in dashboard only.
- Prometheus scrape and evaluation every 5 seconds.
- Active-fleet CPU averaged with vCPU weighting.
- One capacity-changing workflow at a time per protected application.
- No automatic backup restores, and no forced termination of live requests at drain timeout.
- Daily backup jobs do not overlap; infrastructure failure handling takes priority over routine scale-in.
- Conservative local concurrency, short initial monitoring retention, and resource measurement for the 8-vCPU/16-GB management host.
- Native tool authentication/machine credentials are retained where required; no custom login workflow is built.
- Restores require a backup verified within the last 7 days (`recovery.require_verified_backup`, `max_verification_age_days`); backups are verified after capture (`backup.verify`).
- Write requests during maintenance get 503 with `Retry-After: 60` (`maintenance.retry_after_seconds`).
- Supabase Free keep-alive `SELECT 1` every 6 hours.

## Accepted assumptions

- The management VM is assumed always available. There are no off-box copies of state or backups; the single management disk holds both (see docs/12).
- Direct-IP access to app VMs is not blocked (accepted risk; maintenance is still enforced in the app).
- Recovery time targets, to be measured rather than assumed: HAProxy failover under 40 s (3 x 10 s checks plus reload); overload relief via the warm standby under 90 s; one elastic VM ready within 8 minutes; with one retry within 17 minutes.

## Superseded ideas

Do not reintroduce a two-VM cloud cap, AWS services, native cloud-specific load balancers as the cross-cloud entry point, a Kubernetes cluster, a VPN, an Ansible/AWX controller, Pulumi, Restic, Supabase Auth, managed PITR assumptions, three-day scope cuts, or HAProxy routing all management traffic. These were discussed but are not the selected baseline.

## Completion criteria

All required scenarios in docs/10-monitoring-and-tests.md have real execution evidence. Service availability, request errors, provisioning duration, failover duration, and restore correctness must be measured; configuration values alone do not prove performance.
