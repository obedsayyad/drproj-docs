# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Claude Code implementation brief

## Repository state

`collageDRproject/` is the project folder; it holds `drproj-docs/` (this bundle), `drproj-app/` and `drproj-mgmt/` side by side, and each code repo sees this bundle at `docs/`. This bundle is the documentation and contract set; implementation lives in two code repositories, `obedsayyad/drproj-app` and `obedsayyad/drproj-mgmt`, and this bundle is `obedsayyad/drproj-docs`, included in both as a git submodule at `docs/` (see docs/11-build-and-demo-plan.md, GitHub repositories). All three are public; never commit secrets. The bundle is documentation and contracts only: `docs/` (numbered design docs), `config/` (`policy-defaults.json`, `deployment-inputs.example.json`), `spec/` (`openapi.yaml`, `events.schema.json`), and `PROJECT_HANDOFF.md` (combined copy of all docs; regenerate or edit consistently if source docs change). There is no source code, package manifest, build, lint, or test command in this bundle. Add the real commands (including how to run a single test) here or in each component repository's own CLAUDE.md once it exists. The bundle's `SHA256SUMS` will go stale when files are edited.

## Read order

Read README.md, docs/01-requirements-and-decisions.md, docs/02-architecture.md, docs/03-policies.md, docs/04-workflows.md, and docs/12-boundaries-and-deployment-inputs.md before making architecture changes. Consult the remaining documents for the component being implemented.

The user completed technical planning and intends to implement with Claude Code. This bundle is the agreed starting point. Honor the actual implementation/deployment request in the current session. No implementation has been performed by the planning assistant.

## Non-negotiable user choices

- This is a college demonstration, not a production emergency-response deployment.
- Do not constrain the design to the earlier three-day deadline.
- No AWS.
- Node.js backend; Supabase Free PostgreSQL.
- No custom login, Supabase Auth integration, accounts, or application/dashboard RBAC.
- The relief app has no login. Dashboard views are public; every mutating ops method (POST/PATCH/PUT/DELETE) requires the shared Caddy Basic Auth password and returns 401 without it (T31).
- StackStorm is the automation controller. Node.js provides application/dashboard APIs; it must not become a competing infrastructure controller.
- Terraform provisions and removes elastic cloud VMs. Ansible configures machines and deploys containers.
- Fixed primary, management, load-balancer, and testing VMs are self-hosted and have public IPs.
- Exactly one cloud application VM stays warm, on DigitalOcean (default, `capacity.warm_provider`) or Linode; if the warm provider is unavailable the other provider may host an interim warm standby.
- Cloud APPLICATION VM cap: up to 5 DigitalOcean plus up to 5 Linode (`capacity.max_cloud_vms_per_provider`, each 0..5, immutable ceiling 5), including standby, provisioning, uncertain, and cleanup-pending resources. Supporting self-hosted VMs are excluded.
- Scale out/in one VM per step, alternating providers (`elastic_provider_strategy: alternate`) and skipping a provider at cap or unavailable. One slot per possible VM, each with its own Terraform state keyed by slot.
- Each cloud application VM: 1 vCPU / 1 GB RAM.
- Management VM: 8 vCPU / 16 GB RAM, 62 GB disk (Debian 12).
- HAProxy VM: 1 vCPU / 1 GB RAM.
- k6 testing VM: 2 vCPU / 2 GB RAM.
- Docker/Compose on every VM role; no Kubernetes or cross-cloud cluster.
- No firewall setup or firewall automation. No VPN requirement.
- HAProxy balances only sample-application traffic.
- Management interfaces use the management VM's independent reverse proxy.
- Images: GitHub Container Registry; builds: GitHub Actions.
- DNS: Vercel DNS, under cproj.obed.one.
- app.cproj.obed.one points to the HAProxy VM.
- ops, grafana, stackstorm, and stats subdomains point to the management VM.
- Backups: application-schema pg_dump, daily at 00:00 UTC, retained for 30 days, on persistent management storage; unencrypted at rest (dir 0700, files 0600, management VM only).
- Rate limits: HAProxy per-IP stick-table limits (429) apply to the public; the k6 testing VM's IP is exempt (trusted test-host list), while k6's own caps (200 rps, 600 s) and the app's bounded worker pool still apply. The app applies no per-IP limit to the test host.
- No Restic, managed Supabase backup dependency, or PITR assumption.
- Restores are operator-started; first validate in isolation, then restore app data under maintenance.
- Policies are editable. Initial values are in config/policy-defaults.json.

Internal service/API credentials are still needed for providers, StackStorm, database roles, GHCR, and SSH. No-login means no custom user-authentication feature. Keep machine credentials out of browsers, source, logs, and public API output. Native tools may retain their standard API authentication.

## Implementation style

Use TypeScript (pnpm workspaces in `drproj-app` and `drproj-mgmt`), React/Vite frontends, Node.js/Express APIs, node-postgres with bounded pools, database migrations, and Docker Compose. This is a reasonable implementation default, not a mandate to add unrelated frameworks.

Use one relief API deployment and one management API deployment; separate workers/actions live in the StackStorm execution environment. Use shared validation schemas where useful. Keep business modules separate without creating a microservice per feature.

Start fixed VMs as pre-created machines. Bootstrap using Ansible from the developer workstation. Resolve cloud region, provider size/image IDs, and compatible pinned software versions at deployment time. Do not invent actual IPs, credentials, registry namespaces, or account IDs. Approved exception: the operator records the public IPs of the pre-created fixed VMs in the deployment inputs; credentials are never recorded.

Keep Terraform state persistent and separate by provider/failure boundary and keyed per slot. Do not put a Linode fallback behind a Terraform refresh that requires a failed DigitalOcean API. Never use global terraform destroy for scale-in.

Pin image digests and provider/package versions after compatibility verification. Preserve a previous application image for rollback. Database migrations run once in a controlled job, not on every replica startup.

## Critical invariants

1. Reserve a cloud slot transactionally before provisioning. Keep it occupied until deletion is confirmed.
2. A timeout/unreachable API does not prove a VM does not exist.
3. Reconcile a partial/unknown creation before retrying. Stop and raise an incident if safe fallback cannot preserve the per-provider caps (max 5 DigitalOcean, 5 Linode).
4. Primary failure, overload, retry, reconciliation, standby repair, and failback use the same capacity-control lock (lease 120 s, renewed every 20 s; `assert_lease` before each side-effecting call).
5. Use the ready warm standby for overload before provisioning elastic VMs; add one VM per step, alternating providers.
6. Preserve the single warm standby (either provider) after normal recovery. If necessary, preserve the last healthy cloud serving instance until the standby invariant is restored.
7. Do not scale in during a primary outage just because traffic is low. Never scale in the warm standby; remove one elastic VM per step, newest first.
8. Exclude idle standby/supporting VMs from fleet CPU calculations.
9. Do not restart every application container just because Supabase is unavailable.
10. Missing control state or stale metrics cannot authorize speculative scaling/deletion. Freeze CPU scaling only if a serving member is stale/missing or membership is absent; a member HAProxy confirmed down is removed from membership.
11. Management records, maintenance controls, and restore history are outside the app schema being restored.
12. All application writes, including direct-IP requests and background writes, honor maintenance mode.
13. Only configured project-managed resources are eligible for cleanup.
14. Keep real tests and integrations distinct from simulation. Never present fake metrics or mock provisioning as real provider behavior.

## Concurrency and retries

StackStorm execution history is authoritative for workflow task execution; Supabase stores project-level operation references and projections. Terraform state tracks ownership; provider APIs report actual existence; HAProxy reports applied routing.

Use stable operation IDs, distinct attempt IDs, resource tags, database uniqueness constraints, a capacity-lock lease with fencing generation, and reconciliation. Recheck desired state after waiting for a lock. Jobs delayed by concurrency controls must not execute obsolete intent.

Configure concurrency controls explicitly. StackStorm concurrency policies need supporting coordination configuration; merely installing StackStorm is not an exactly-once or race-free guarantee. A database slot reservation remains required.

Confirmed provisioning defaults: initial checkpoint at 240 s; rechecks at 360 and 480 s; fail at the 480 s deadline if not ready; one retry per provider after confirmed cleanup. Accept early readiness. When both providers exhaust attempts, stop provisioning and raise an incident.

## Policy edits

Validate, persist a pending version, compile affected tool configuration, validate/reload, and verify it before activation. On failure retain/reinstate the previous active version. Alert events carry policy version and timestamp. Running workflows capture a version; capacity reductions are staged until compatible with existing reserved capacity.

Policy edits (like every mutating ops call) require the shared Caddy Basic Auth password, not a user login or accounts. Validate allowed commands and parameters regardless. Do not expose an arbitrary shell or arbitrary Terraform file path in the public API.

## Build gates

Follow docs/11-build-and-demo-plan.md (phases are dependency order):
1. Phase 1: contracts and app data (workspace, app/ops migrations, relief API/UI, idempotency, maintenance admission).
2. Phase 2: management API/dashboard and versioned policy activation.
3. Phase 3: Compose and genuine monitoring on the management VM.
4. Phase 4: StackStorm workflows, leases/slots, local contract tests.
5. Phase 5: real infrastructure (per-slot Terraform states, Ansible roles).
6. Phase 6: HAProxy routing, activation/drain/failover/failback.
7. Phase 7: backups, verification, and staged restore.
8. Phase 8: end-to-end demonstrations and measured evidence.

Run targeted meaningful checks at each gate. Do not write tests that merely mirror constants or repeatedly test unchanged behavior. Ask only for actual missing inputs, substantive unresolved requirements, or authorization outside the current request. The user prefers reasonable defaults over questions about every knob.

## Deliverables expected from implementation

Working source, migrations, validated Compose files, pinned images/tool versions, Terraform modules and persistent state configuration, Ansible roles, StackStorm pack, Prometheus/Alertmanager/Grafana configuration, CI, seeded demo data, k6 tests, runbooks, and actual validation evidence. Keep this documentation updated when implementation resolves an engineering default.

Health checks: `inter 10s fall 3 rise 6` on the primary server line (with `timeout check 4s` in `defaults`/`backend`, never on the server line) (rise 2 on standbys/elastic), `/health/live` only; `/health/ready` never removes a backend. Failback: direct probes of the primary while in maint, weight 0 set before `ready`, then ramp 10% -> 50% -> 100% (observe 60/60/120 s) with gates, reversing on regression. The management VM is assumed always up (accepted risk, docs/12). RTO targets to be measured: HAProxy failover < 40 s, overload relief < 90 s, one elastic VM < 8 min, with one retry < 17 min. Writes during maintenance get 503 + `Retry-After: 60`; restores need a backup verified within 7 days; restore preflight requires `current_db_size + restored_app_size * 1.2 < 450 MB` (Free limit 500 MB), with pg_dump/pg_restore/psql and the restore-test Postgres image pinned to the same major as the Supabase server (`postgres_server_major`).

Do not claim full regional recovery or Supabase-provider failover. Single management/LB VMs and shared Supabase are accepted boundaries described in the docs.
