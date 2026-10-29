# Boundaries, defaults, and actual deployment inputs

## Accepted demonstration boundaries

- One HAProxy VM: its own failure interrupts application ingress.
- One management VM: controller/monitoring capacity is not highly available.
- All fixed VMs are self-hosted; physical host independence is not guaranteed.
- One shared Supabase Free project/database dependency: compute-provider failover does not provide database-provider failover.
- Backups reside on management persistent storage; loss of that storage is outside the app-data backup demonstration.
- The relief app has no login; ops dashboard views are public and mutating operations require the shared operator password (Caddy Basic Auth). No accounts, no application RBAC, no Supabase Auth, no firewall automation, no VPN. Do not reinterpret this as permission to leak machine secrets.
- Native tool APIs still use machine credentials. Native tools are not replaced by custom login systems.
- No zero-data-loss/instant-recovery guarantee; actual RTO/RPO are measured.
- No whole-region recovery claim, multi-cloud Kubernetes, or physical relief-inventory optimizer.
- Supabase Free exports cover app data; they are not a complete Supabase service/Auth/Storage backup.
- Provider instance creation is not instantaneous; one warm standby (DigitalOcean by default, `capacity.warm_provider`) reduces compute failover delay.
- If uncertain resource existence prevents safe provisioning within a provider's slots (cap 5 each for DigitalOcean and Linode), stop and report the incident rather than promise automatic fallback.
- Management host sizing is fixed by user preference; validate resource headroom before claiming readiness.

## Residual risks (accepted)

| Risk | Mitigation |
|---|---|
| Dashboard views are public, so GETs on ops expose operational state | Mutations require Caddy basic auth; `public_ip` and `provider_resource_id` redacted in public responses; no secrets in GET payloads |
| Shared operator password, no per-user audit | Audit actor recorded as `operator`; bcrypt hash from env; rotate password after the demo |
| Direct-IP access bypasses HAProxy | Maintenance, rate limit, and body limit enforced in the app too; accepted, not blocked |
| Public `/api/demo/workload` | Gated by `DEMO_MODE` flag and rate limit (5/10 s) |
| k6 testing host exempt from rate limits | HAProxy skips tracking for the testing host IP (`rate_limit_exempt_ips`, generated from deployment inputs) and the app skips its per-IP limit; k6's own caps (200 rps, 600 s) and the bounded worker pool still apply; public limits stay for all other IPs |
| Backups unencrypted at rest on the management VM | Plain `pg_dump` custom format, dir 0700 and files 0600, management VM only; automatic verification needs no keys; demo data only |
| Spam on the login-free relief app | HAProxy stick-table rate limits (429), 8 KB body limit, quantity max 10000, idempotency keys; no CAPTCHA |
| Real personal data entered | Demo data only; UI and docs state no real personal information |
| Management VM assumed always up | No LB-side guard; reconcile and Prometheus run there; restart it first in any recovery |
| One disk holds Terraform state and backups, no off-box copy | Backups dir 0700, files 0600; verify backups regularly; loss of the disk is outside the demo |
| HAProxy VM is a single point of failure | Manual recovery runbook (docs/15), TTL 60 s on the app record, LB config/cert backup on the management VM (0700/0600) |
| Plaintext `.env` secrets on management VM | Mode 0600 files in 0700 dirs, scoped expiring provider tokens, never committed |

## Defaults that were chosen by engineering judgment

TypeScript/React/Vite/Express; Caddy management proxy; acme.sh app certificate helper; weighted active CPU; one-minute reconciliation; one-minute drain deadline; dashboard-only notifications; five-second scrape/evaluation; 30-second metric staleness; gradual 10/50/100 failback; initial 3-day Prometheus retention; serialized infrastructure jobs; isolated PostgreSQL restore target; limited connection pools.

These are reasonable implementation defaults, not additional explicit user answers. Keep them configurable where appropriate. Do not reopen them as questions unless compatibility or actual requirements make a change necessary.

The four fixed hosts run Debian 12 (bookworm, x86_64): management 8 vCPU/16 GB/62 GB disk, primary 2 vCPU/2 GB, load balancer 1 vCPU/1 GB, testing 4 vCPU/8 GB (measured at BOOTSTRAP-1). The bootstrap role supports Debian 12, Debian 13 and Ubuntu (Docker repository `download.docker.com/linux/<distribution>`, suite line from `ansible_facts['distribution_release']`: bookworm or trixie; Docker 29.5.2 exists for both, `5:29.5.2-1~debian.12~bookworm` and `5:29.5.2-1~debian.13~trixie`). **OS difference:** the four fixed hosts run Debian 12; cloud VMs run Debian 13 (trixie) on both providers, because DigitalOcean no longer offers `debian-12-x64`. Provider inputs for the demo (non-secret): DigitalOcean region `fra1`, size `s-1vcpu-1gb`, image `debian-13-x64`, SSH key name `drproj` (id in `env://DIGITALOCEAN_SSH_KEY_ID`); Linode region `eu-central`, type `g6-nanode-1`, image `linode/debian13`, SSH key label `drproj` (`env://LINODE_SSH_KEY_LABEL`). The Terraform slot modules have no image default; `image` always comes from these inputs. Ubuntu 22.04 LTS was the original management OS candidate matching the consulted StackStorm documentation. Verify current supported versions and provider images at implementation time. Do not treat a documentation candidate as permission to install obsolete/incompatible dependency combinations.

## Runtime environment variables (resolved defaults)

| Variable | Service | Default | Behavior |
|---|---|---|---|
| `APP_DB_POOL_MAX` | relief app | 2 | Maximum database connections per instance. Integer 1..5 (the policy `runtime.app_db_pool_max` ceiling); any other value makes the app refuse to start with an error naming the variable. Nothing is clamped silently |
| `OPS_OPERATOR_PASSWORD` | management API | none | Shared operator password (at least 12 characters) enforced on every mutation, the same credential Caddy checks. Required: the API refuses to start without it unless `OPS_TRUST_PROXY_AUTH=true` |
| `OPS_TRUST_PROXY_AUTH` | management API | `false` | Explicit opt-in to run without `OPS_OPERATOR_PASSWORD` and rely on Caddy basic auth alone (the container port must then be reachable only through Caddy). A warning is logged at start |
| `OPS_BASIC_AUTH_HASH` | Caddy (management) | none | bcrypt hash of `OPS_OPERATOR_PASSWORD`, derived with `caddy hash-password` and kept in step with it (docs/08). Required: Compose refuses to start without it. Single-quote it in `.env` so Compose does not interpolate `$` |
| `OPS_METRICS_PORT` | management API | 9465 | Internal `/metrics` listener (integer 1..65535, must differ from `OPS_PORT`). Reachable on the Compose network only; never published or routed through Caddy |
| `OPS_DB_POOL_MAX` | management API | 3 | Maximum database connections. Integer 1..5; any other value makes the API refuse to start with an error naming the variable. Nothing is clamped silently |
| `OPS_MAX_SSE_STREAMS` | management API | 20 | Concurrent progress streams (1..200); more are refused with 503 `TOO_MANY_STREAMS` |
| `OPS_SSE_POLL_MS` | management API | 2000 | Progress stream poll interval in ms (100..10000) |
| `PROMETHEUS_EXPECTED_JOBS` | management API | `prometheus,ops,alertmanager` | Comma-separated scrape job names (`^[a-z0-9_-]+$`) whose targets must all be healthy and fresh for the overview's Prometheus integration to report `fresh` (docs/06). Add `relief` once application VMs are scraped (Phase 5) |
| `MGMT_SECRETS_DIR` | management Compose | `./secrets` | Directory holding the secret files listed in `deploy/compose/mgmt/secrets/README.md` (mode 0700, files 0600). Smoke scripts point it at a throwaway directory so they never touch operator secrets |
| `ST2_PACK` | management API | `cdr` | StackStorm pack name for dispatched actions (`^[a-z0-9_]{1,40}$`) |
| `TRUSTED_PROXY_IPS` | relief app | empty | Comma-separated proxy addresses; `X-Forwarded-For` is trusted for the rate-limit client IP only when the socket peer is listed |

**StackStorm `cdr` pack settings** (`/opt/stackstorm/configs/cdr.yaml`, schema `automation/stackstorm/packs/cdr/config.schema.yaml`; secrets stay in the environment, the file holds only names and paths):

| Key | Meaning |
| --- | --- |
| `provider_adapter` | Required, no default: `simulation` or `real`. Selecting `real` needs the `real_adapter` object; without it the action fails `provider_adapter_missing_or_unknown` and nothing runs |
| `real_adapter.tf_slot_script` | Absolute path of `scripts/tf-slot.sh` on the runner |
| `real_adapter.tf_state_dir` | Terraform state root (`cloud_application.terraform.tf_state_dir`, default `/var/lib/drproj/terraform`, mode 0700) |
| `real_adapter.terraform_bin` | Optional binary override (tests only; the runner image uses `PATH`) |
| `real_adapter.digitalocean.*`, `real_adapter.linode.*` | All required: `token_env` (`env://DIGITALOCEAN_API_TOKEN` / `env://LINODE_API_TOKEN`, the `token_reference` of the deployment inputs), `region`, `size` (`size_id`), `image` (`image_id`), `ssh_key_id_env` (the `ssh_key_id_reference`). Unknown keys are rejected, so a literal token cannot be added by mistake |

The StackStorm config schema cannot make `real_adapter` conditional on `provider_adapter: real`; the adapter loader enforces that, and the schema rejects an incomplete `real_adapter` object. `region`, `size` and `image` come from the deployment inputs and are never invented.

The `versions.*` fields in `config/deployment-inputs.example.json` hold the pinned tags used by the Compose files (`versions_status` is "unverified until smoke"). `versions.docker` is the Docker CE upstream version the `bootstrap` role pins (apt glob `5:<version>-*`, held); it is unverified until the first bootstrap, like the other tags. (P5.3 changed this from null.) `allow_local_tag` is false for every real VM. `versions.ansible` is the ansible-core 2.17 line, because newer releases need a newer Python than the StackStorm 3.9 image provides (3.10).

## Local deployment inputs file

`config/deployment-inputs.example.json` in this bundle now carries the approved addresses of the four fixed hosts (management 103.141.79.170, primary .171, load balancer .172, testing .173), the bootstrap user (`root`, first run only), the key path `~/.ssh/drproj` (a path on the workstation, not key material) and `rate_limit_exempt_ips` (the testing host). This departs from "no IPs in repositories" and was approved by the user for this public documentation repository; secrets, tokens and hashes remain out of every file. The runtime copy is `drproj-mgmt/deploy/inputs/deployment-inputs.local.json`: gitignored, mode 0600, created from the example with `documentation_only` false, and the only file Ansible and the runner read. Change values there; keep the example's keys in step. On the management VM the layout is fixed: the controller checkout is `/opt/drproj/ansible` (venv `/opt/drproj/ansible-venv`, scripts `/opt/drproj/scripts`) and the inputs file sits beside it at `/opt/drproj/ansible/deployment-inputs.local.json` (mode 0600). `inventory/group_vars/management.yml` records that path, and `group_vars/all.yml` uses it as the default `inputs_file` when the management host is the local connection (`gen-inventory.py --local-role management`); elsewhere the default stays the repo-relative file, and `-e inputs_file=...` always wins. There is no `/opt/drproj/deploy` on management.

## Deployment inputs, not unanswered architectural questions

The future implementation will need:
- Actual public IPs/SSH details for primary, management, load balancer and testing VMs. The testing host IP also feeds `rate_limit_exempt_ips` (HAProxy trusted test list and the app's per-IP limit exemption).
- Actual existing fixed-primary size and disk capacity, and management persistent disk capacity.
- Per-provider caps (`capacity.max_cloud_vms_per_provider`, each 0..5; default 5 and 5) and `capacity.warm_provider` (`digitalocean` or `linode`, default `digitalocean`).
- Available disks for management state/backups/monitoring; choose/configure sufficient persistent capacity and report constraints.
- DigitalOcean/Linode accounts, scoped API credentials and supported regions.
- Resolve 1-vCPU/1-GB provider size IDs and supported OS images from their APIs.
- Use suitable nearby regions for the demo, preferably appropriate to the user's India location when available; actual account/API availability is authoritative.
- Supabase project reference and appropriate runtime/migration/dump connection credentials.
- GitHub namespace and GHCR image names are decided and recorded in `registry` in the example and local inputs: `registry.namespace` = `obedsayyad`, `registry.images` = `relief: drproj-relief`, `operations: drproj-operations`, `runner: drproj-runner` (the legacy `registry.repository` stays null; see docs/11-build-and-demo-plan.md, GitHub repositories). Registry pull credentials are optional because the images are public.
- SSH public/private key references (including the key installed on cloud VMs) and deployment runner access. Per provider, `cloud_application.<provider>.ssh_key_id_reference` names the key already registered in that account (DigitalOcean key id or fingerprint; Linode SSH-key label).
- `backup.dir` (default `/var/backups/drproj`, mode 0700, management VM only; exported as `BACKUP_DIR` for `scripts/check-perms.sh`),
- `cloud_application.terraform.tf_state_dir` (default `/var/lib/drproj/terraform`, mode 0700; `TFSTATE_DIR` for `scripts/check-perms.sh`), and the pinned `versions.terraform_sha256_linux_amd64`, `terraform_provider_digitalocean`, `terraform_provider_linode`, `ansible_collections`, `tflint` and `bats`.
- Ports (8443, 9100, 8404), the backend TLS trust bundle reference, an ACME staging flag, and the restore connection reference.
- Supabase `dump_connection_mode` (`session_pooler`) and `postgres_server_major` (pin pg_dump/pg_restore/psql and the restore-test Postgres image to the same major as the Supabase server).
- Supabase facts recorded by DB-1: `supabase.project_ref` = `dhppvnbwxkfrktjkrsqp` (region ap-southeast-1, session pooler `aws-0-ap-southeast-1.pooler.supabase.com:5432`), server PostgreSQL 17.11, so `postgres_server_major` = 17 (restore-test image `postgres:17.x`). Pooler usernames are `<role>.<project_ref>`. The pooler's chain roots at "Supabase Root 2021 CA"; management keeps that public root at `/etc/drproj/ca/supabase-root-2021.crt` for Node (`NODE_EXTRA_CA_CERTS`), so TLS verification stays on. Passwords exist only in `/etc/drproj/secrets/db-<role>.env` (mode 0600, management VM; roles `migration`, `app_runtime`, `ops`, `backup`, `restore`, `automation`), never in repositories, inputs files, evidence or logs. Login role `restore` is a member of `dr_restore`, which owns schema `app`; the Supabase `postgres` user is not a true superuser (`rolsuper` = f) but has CREATEROLE, which is enough.
- Vercel A-record values once host IPs are known.
- ACME account contact email and compatible pinned tool/container versions.

Secret references use the form `env://NAME`, pointing at `.env` values on the management VM. Never put real credentials into these documentation files. config/deployment-inputs.example.json deliberately contains nulls/placeholders. Provisioning cannot be honestly demonstrated against a provider until its actual account inputs are supplied.

## What must be measured

CPU workload calibration, real VM provisioning time under the 8-minute readiness deadline (checkpoints 240/360/480 s), container resource usage on 8-vCPU/16-GB management, DNS/TLS success, actual Supabase pooling constraints, full round-trip app readiness, and successful pg_dump/restore against the source schema/version.

If a provider routinely takes longer than the chosen deadline, report that evidence and use the editable policy; do not falsify milestone events or secretly move the user's timeout.
