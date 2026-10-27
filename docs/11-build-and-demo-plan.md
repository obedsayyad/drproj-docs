# Build plan and demonstration sequence

This is dependency ordering, not a deadline-based scope reduction. Every acceptance row in docs/10 (T01 to T36) is owned by exactly one phase below.

## Phase 1 — contracts and app data

Initialize TypeScript workspace, document environment inputs, migrate app/ops schemas, implement relief CRUD, validation, transactional idempotency, maintenance admission, and instance identity. Seed deterministic demo data. Security: request body limit 8 KB, quantity maximum 10000, Idempotency-Key pattern `^[A-Za-z0-9_-]{8,128}$`, security headers, redacted `public_ip`/`provider_resource_id` in public instance responses, `/api/demo/workload` gated by the DEMO_MODE flag, demo data only.

Gate: repeated/concurrent submissions create one business record; status transitions work; maintenance blocks writes even via a direct VM address (503 `MAINTENANCE_WRITES_BLOCKED`, fail closed when the flag is unreadable).

Tests: T02, T19, T25.

## Phase 2 — management API/dashboard

Implement versioned policies, operations/catalogue projections, incidents, VM inventory, tests and backup screens. No login/roles. Source real tool data; show stale/unavailable integrations.

Gate: invalid policy/version conflicts fail correctly; accepted work returns operation IDs; secrets never enter frontend outputs.

Tests: T11, T24.

## Phase 3 — Compose and monitoring

Create role-specific Compose files, health checks, persistent storage and pinned services. Bring up StackStorm and native stores, Caddy, ops services and monitoring on the selected management capacity. Security hardening: Caddy `basic_auth` on mutating ops methods, Grafana anonymous access and sign-up off with the admin password from a file, StackStorm auth enabled with `st2admin` changed and an API key for Alertmanager, Prometheus/Alertmanager on the Compose network only, node_exporter on 9100 with TLS and basic auth, `.env` files 0600 in 0700 directories, scoped expiring provider tokens, backup and Terraform-state permissions. Management VM memory: per-container `mem_limit`, one heavy job at a time, 2 GB swapfile, one actionrunner. Start the Supabase keep-alive and size/cert-expiry alerts.

Gate: Compose validation, dependency health and real metrics; memory/concurrency measured under one heavy job; mutating requests (POST/PATCH/PUT/DELETE) on the ops host without the shared password return 401.

Tests: T16, T18, T30, T31.

## Phase 4 — StackStorm workflows

Create typed events/actions, capacity leases/slots, local adapters and execution history projection. Configure coordination explicitly. Implement bounded retries, readiness checkpoints (240/360/480 s), rechecking stale queued intents and reconciliation, lease heartbeat and `assert_lease` before side effects, per-slot Terraform states, tag-based orphan scan (never delete by tag alone), no scale-in during a primary outage, and the warm standby repair workflow. The "Adopt tagged orphans" runbook already exists in docs/15-runbooks.md section 1; update it alongside this work rather than writing it.

Gate: concurrency/failure contract tests preserve slots; restart/timeout does not duplicate a create; unknown tagged resources raise an incident.

Tests: T08, T09, T10, T22, T27, T36.

**Status: complete on simulation (26 October 2026).** Gate and tests T08, T09, T10, T22, T27 and T36 pass against the local simulation adapter: a throwaway Postgres 17 container and StackStorm 3.9.0 (emulated amd64) under Docker Desktop, every VM a row in the simulation adapter's JSON state, routing, drain and the Prometheus reload simulated. This is not real infrastructure. Scripts, assertions and the latest pass counts are in `drproj-mgmt/tests/compose/README.md` and `PHASE4-EVIDENCE.md`; behaviour is in docs/04 (warm standby repair, as implemented) and docs/07 (resolved engineering defaults P4.2, P4.3, P4.4). Deferred, with where each lands:

- **Real provider adapter** (Terraform/Ansible-backed create, get, delete, list-by-tag, power on, deploy, readiness, pinned digest): P5. Until then `provider_adapter: real` reports `not_implemented`.
- **Real routing and HAProxy drain** (`apply_routing`, `drain_instance`, the HAProxy view in `standby_verify`): P6.
- **Real `standby.unhealthy` producer** (HAProxy health of the standby server): P6. P4 has the simulation action `cdr.standby_unhealthy` only.
- **apply_policy compile targets**: HAProxy, k6, Prometheus alert-rule thresholds and StackStorm timers (P6, P7); the compose wiring that lets StackStorm write `prometheus.yml` and reload Prometheus (`--web.enable-lifecycle`, a writable mount) is P6. Changes to those settings fail `not_implemented` until then.
- **Workflow bodies for** `failback` (P6), `start_load_test` and `stop_load_test` (P6/P8), `create_backup`, `verify_backup` and `restore_application` (P7): skeleton workflows exist and fail `not_implemented`.
- Not covered by P4: the restart gate against a real provider, per-slot Terraform states and TLS rotation (P5).

## Phase 5 — real infrastructure

Implement isolated Terraform provider states and Ansible roles. Start with fixed self-hosted VMs and one protected warm instance on the warm provider (DigitalOcean by default). Deploy the same digest everywhere; the deploy action refuses a `:local` (undigested) image reference unless `allow_local_tag` is true, which is for local and simulated runs only and is never set for a real provider VM. Enforce per-provider caps (5 DigitalOcean, 5 Linode) with one slot per possible VM, adding one VM per step and alternating providers. Rotate the backend TLS CA/leaf certificates through Ansible. Store actual provider inputs separately from defaults.

Gate: actual VM creation, deployment, readiness and deletion evidence; per-provider caps enforced throughout.

Tests: T28, T29.

## Phase 6 — routing

HAProxy primary-first, backup activation, overload mode change, health failure routing, persistent membership, safe drain and gradual failback. Independent management Caddy/DNS/TLS path. Security hardening: `/health/live`-only checks, stick-table rate limits with 429 (k6 testing host IP exempt via a trusted-IP list; k6 caps and the app worker pool still apply), unique request IDs, `stats auth` over TLS, server-state file persisted for failback gating. Runbooks (docs/15-runbooks.md): HAProxy VM recovery (60 s TTL on the app A record, LB config backup, Let's Encrypt staging for tests).

Gate: app and management DNS reach different VM roles; existing traffic survives management controller restart; exact deletion waits for drain/provider confirmation; failback reverses on regression; failback sets primary weight 0 before leaving maint, using direct probes while in maint.

Tests: T01, T03, T04, T05, T06, T07, T15, T17, T21, T23, T26, T32, T33.

## Phase 7 — backups/recovery

Daily 00:00 UTC app dump (plain custom format, unencrypted, dir 0700 / files 0600 on the management VM) with 30-day retention, catalogue/checksum, isolated restore verification, maintenance, safety dump, live app-only restore, validation and rollback. Restores require a verified backup no older than 7 days and a size preflight: `current_db_size + restored_app_size * 1.2` under 450 MB; pg_dump/pg_restore/psql and the restore-test image pinned to the same major as the Supabase server. Runbooks: restore and rollback steps in docs/15-runbooks.md.

Gate: known deleted demo data recovered; ops/maintenance logs preserved.

Tests: T12, T13, T14, T20, T34, T35.

## Phase 8 — final demonstrations

1. Show normal primary-first state and ready DO standby.
2. Start k6; show sustained load, warm standby activation, then elastic VM creation (one per step, alternating providers) if needed.
3. Keep offered workload steady; compare CPU/latency/errors.
4. Stop primary; show cloud traffic and redundancy.
5. Restore primary; show stable observation, gradual 10%/50%/100% return and safe scale-in.
6. Create a fresh app backup, delete known test data, and run verified recovery.
7. Show a controlled failed/ambiguous provisioning event and per-provider cap protection.
8. Show policy editing and operation version history.

Tests: re-run and record evidence for T03 to T07 and T13 (owned by earlier phases); run the Supabase wake check (T30) at T-24h and T-1h; follow docs/15-runbooks.md for any manual recovery.

Use different operation IDs per scenario. Finish by stopping k6, verifying baseline routing, preserving the warm standby, and deleting only eligible elastic resources.

## GitHub repositories

Decided 2026-10-22. Owner: GitHub account `obedsayyad`. Visibility: public. Three repositories:

| Repository | Contents | Publishes |
|---|---|---|
| `obedsayyad/drproj-docs` | This bundle as is: numbered docs, `spec/` (OpenAPI, events schema), `config/`, `PROJECT_HANDOFF.md`, `SHA256SUMS`, runbooks | Nothing (source of truth for contracts) |
| `obedsayyad/drproj-app` | `apps/relief` (UI, API, demo workload), `packages/contracts`, `database/migrations` (one ordered runner: roles, `ops`, then `app`), `deploy/compose/app`, `tests/integration` | `ghcr.io/obedsayyad/drproj-relief` |
| `obedsayyad/drproj-mgmt` | `apps/operations` (dashboard, management API), `infra/terraform` (one state per slot), `infra/ansible`, `automation/stackstorm`, `deploy/compose/mgmt`, HAProxy/Caddy config, `monitoring`, `tests/load` (k6), mgmt integration tests | `ghcr.io/obedsayyad/drproj-operations`, `ghcr.io/obedsayyad/drproj-runner` |

### Shared contracts

- `drproj-docs` is a git submodule at `docs/` in `drproj-app` and `drproj-mgmt`, pinned to a commit. Existing paths such as `docs/spec/openapi.yaml` keep working.
- Each code repo generates its own types from `docs/spec` (`pnpm gen:api`) and keeps a compile-time drift check against them. No shared npm package.
- Contract change order: merge the spec change in `drproj-docs`, then bump the submodule in each code repo by PR and fix what the drift check reports.
- Migrations stay in `drproj-app` (single runner, so roles, `ops` and `app` apply in order), including ops-schema changes the management API needs. `drproj-mgmt` does not run its own migrations; its integration tests apply `drproj-app`'s through a `file:` dependency on `../drproj-app/database/migrations`, so `drproj-mgmt` CI checks out `drproj-app` next to it.

### Local layout

The project folder holds the three checkouts side by side (restructured 2026-10-23):

```
collageDRproject/
├─ drproj-docs/   this bundle
├─ drproj-app/    relief app, contracts, all migrations
└─ drproj-mgmt/   management API and dashboard; later infra, automation, monitoring, k6
```

Each code repo includes `drproj-docs` at `docs/`. `docs` is a git submodule (`git submodule add https://github.com/obedsayyad/drproj-docs docs`); after cloning run `git submodule update --init`. Run pnpm commands inside a repo folder.

### Branches and CI

- `main` is protected: changes land by pull request only, required CI checks must pass, no force pushes or deletion. Zero required approvals (single developer may self-merge).
- `drproj-docs` CI: JSON syntax for `config/` and `spec/events.schema.json`, Redocly lint of `spec/openapi.yaml`, `SHA256SUMS` check.
- `drproj-app` CI: `pnpm typecheck`, `pnpm test`, `pnpm test:integration` against a throwaway Postgres service container (same major as Supabase), image build. On `main`, publish the relief image tagged with the commit SHA; deployments pin the digest.
- `drproj-mgmt` CI: check out `drproj-app` alongside (shared migrations), typecheck and tests, `terraform fmt -check` and `terraform validate` (no provider credentials, no plan/apply), `ansible-lint`, `docker compose config`, `promtool check rules`, image builds; on `main`, publish the operations and runner images.
- CI never deploys or touches cloud accounts. Deployment stays with Ansible from the workstation and StackStorm on the management VM.

### Secrets (public repositories)

- No secrets in any repository, including `.env` values, Terraform state, tfvars with real values, or SSH keys. Approved exception: the public IPs of the fixed VMs are recorded in the deployment inputs by operator approval; credentials never are. Cloud VM IPs and invented IPs stay out. `.gitignore` covers `.env*` (except `.env.example`), `*.tfstate*`, and `*.tfvars` (except examples).
- CI uses only the built-in `GITHUB_TOKEN` with `packages: write` to publish to GHCR; no stored Actions secrets are needed.
- Use GitHub-hosted runners only. Never attach a self-hosted runner to these public repositories.
- Runtime secrets stay in `.env` files (mode 0600) on the management VM, referenced as `env://NAME`.
- GHCR packages are public, so hosts can pull images anonymously; `ghcr-readonly` is optional.

## Bootstrap

From the developer workstation, configure pre-created self-hosted hosts with Ansible. Initialize Supabase schemas and ops seed policy, publish images to GHCR, start management services, then use StackStorm for cloud baseline/elastic resources. Deploy HAProxy and TLS after actual DNS is correct. Enable timers only after a successful smoke run.

Ansible bootstrap commands (P5.3; written, **not run**). From `drproj-mgmt`, after the first-contact host keys are in `known_hosts` and `~/.ssh/drproj` has been checked (docs/15, "First bootstrap"):

```sh
# Run on the management VM (docs/15, First bootstrap), from /opt/drproj/ansible. The fixed hosts are operated as root (approved deviation).
python3 scripts/gen-inventory.py --inputs deployment-inputs.local.json --bootstrap --local-role management --output inventory/generated.yml
ansible-galaxy collection install -p /opt/drproj/ansible-venv/collections -r requirements.yml
ansible-playbook -i inventory/generated.yml playbooks/bootstrap.yml --limit management          # first, alone
ansible-playbook -i inventory/generated.yml playbooks/bootstrap.yml --limit primary,lb,testing
ansible-playbook -i inventory/generated.yml playbooks/ca-init.yml
ansible-playbook -i inventory/generated.yml playbooks/ca-init.yml
```

`playbooks/app.yml` (primary and cloud VMs) is run by `cdr.run_heavy` for cloud VMs; for the primary it is run by hand once the image digest is in the inputs.

## Releases

CI builds/tests/publishes a pinned image. Roll out compatible releases to the warm instance first, verify, then update the primary and any active elastic instances with controlled routing. Record revision and rollback image. Run database migrations separately; do not infer that image rollback can undo a destructive migration.

### Release log (CI-1)

CI publishes on every push to `main` (`drproj-app` and `drproj-mgmt` `.github/workflows/ci.yml`): tests first, then a linux/amd64 build pushed as `ghcr.io/obedsayyad/<image>:<full commit sha>` and `:main`, with the manifest digest in the job summary. Packages are public (they inherit the public repository through the `org.opencontainers.image.source` label; an anonymous pull token works). Deployments pin the digest recorded in `registry.*_image_digest` (`config/deployment-inputs.example.json` and the local copy, also synced to `/opt/drproj/ansible/deployment-inputs.local.json` on management). CI never deploys. The runner image is smoke-tested with `docker run --network none ... terraform init -backend=false` in both slot roots before it is pushed, so provider resolution from the offline mirror is proven on native amd64 (closes OPEN-P5.2-1).

| Image | Source commit | Digest | Rollback |
|---|---|---|---|
| `ghcr.io/obedsayyad/drproj-relief` | `drproj-app` 71ba435 | `sha256:4427a3a781edc93c0c09ad7da560ffb2659a28d3e6dda74e912c658259adeaea` | none yet (first publish) |
| `ghcr.io/obedsayyad/drproj-operations` | `drproj-mgmt` e0647d7 | `sha256:b12ee869712a8176c659d2666846c3ff47ede30d579949220b780fcadafa546b` | none yet (first publish) |
| `ghcr.io/obedsayyad/drproj-runner` | `drproj-mgmt` e0647d7 | `sha256:dcf8f1babe5fa3f50fc18188a5363518d6967a0004ec6dc8133d50ff6ed97eb7` | none yet (first publish) |

When a later push publishes a new digest, copy the previous digest into the Rollback column before replacing it in the deployment inputs. Pushes that change only the `docs` submodule rebuild an image with the same contents but a new digest (revision label); record the digest you actually deploy.

