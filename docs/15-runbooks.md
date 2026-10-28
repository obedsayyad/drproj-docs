# Operational runbooks

Manual or semi-automated procedures. Run them from the management VM. Related: docs/08 (HAProxy, TLS), docs/09 (backup and restore), docs/12 (accepted risks).

## 1. Adopt tagged orphans

_Final for Phase 4 (local simulation; the same logic runs against real providers in P5): reconcile never deletes. An ambiguous create holds its slot `held_uncertain` and opens `provision.uncertain:<slot>`; a warm standby that cannot be classified (provider answer unknown) is held the same way and opens `standby.uncertain:<slot>` (docs/04, warm standby repair). Reconcile resolves such a slot itself when the provider API answers (one resource matching a recorded attempt of that slot: slot `bound`, instance `failed` until the repair or redeploy confirms it; none listed: slot `free`). A tagged resource nothing explains opens `orphan:<provider>:<resource id>` (critical) and, if its `slot` tag names a free slot of that provider, holds that slot `held_uncertain`, counted against the cap. A `releasing` slot whose VM still exists opens `release.pending:<slot>`. A `slot=interim-warm-<provider>` resource is the interim warm standby; it is released only after the warm slot is `bound` and `ready` again, by the scale-in reconcile requests (`reconcile.surplus_warm:<slot>`), with provider-API absence evidence. A warm slot that stays `held_uncertain` with no interim standby gets a repair request from reconcile at most once per backoff (twice the lease length). Steps 5 and 6 below are what the operator does with those incidents; the incidents resolve by operator action after the resource is adopted, deleted or confirmed absent._

Use when reconcile finds provider resources tagged `project=cdr` that no operation or slot explains.

1. Take the capacity lock; pause scale actions.
2. List resources by tags `project=cdr`, `slot=<key>`, `op=<operation_id>`, `attempt=<n>` through the provider API.
3. If a StackStorm execution with the same `operation_id` is still running, adopt it; do not start another.
4. Compare with Terraform state for that slot and with the slot table. A resource tagged `slot=interim-warm-<provider>` belongs to the interim warm standby slot (see the warm standby repair workflow in docs/04); it counts within that provider's cap until reconcile resolves the warm slot.
5. Known slot, state missing: import into that slot's workspace (`terraform import`), then redeploy and check `/health/ready`.
6. Unknown tag combination: reconcile has already marked the slot `held_uncertain` (when the `slot` tag names a free slot) and raised the `orphan:<provider>:<id>` incident, and stops there. Never delete by tag alone. An operator confirms ownership before any delete; after deleting through the provider (or adopting it into the slot's Terraform workspace), the next reconcile finds the slot's resource absent and frees it (or finds it matching and binds it), and the operator resolves the incident.
7. Release the lock only when the slot state and Terraform state agree.

## 2. HAProxy VM recovery

Manual runbook; the HAProxy VM is a single point of failure (docs/12).

Prepare in advance:
- Keep TTL at 60 s on the `app.cproj` A record in Vercel DNS.
- Keep a plain backup of the HAProxy config, the certificate bundle, and the acme account on the management VM (directory 0700, files 0600). The `lb` role writes it after every successful run to `<backup.dir>/lb/` (`/var/backups/drproj/lb/`): `drproj-lb-backup.tgz` (`haproxy/`, `certs/`, `.env`, `compose.yaml`) and `drproj-lb-acme.tgz` (the acme.sh home with the account and certificate).

Recover:
1. Choose a replacement host (new or existing VM).
2. Restore config, certificate bundle, and acme account from the backup.
3. Unpack both archives into `/opt/drproj/lb` (and the acme archive into the `acme` volume, or skip it and accept a new issue), then run the Ansible `lb` role against the new host (`playbooks/lb.yml`; section 9).
4. Test before DNS change: `curl --resolve app.cproj.obed.one:443:<new-ip> https://app.cproj.obed.one/health/live`.
5. In Vercel DNS, repoint the `app.cproj` A record to the new IP. Recovery time is bounded by the 60 s TTL plus resolver caching.
6. Verify via public DNS, then check HAProxy server states and `show servers state`.

Let's Encrypt limits: reissuing repeatedly can hit rate limits (duplicate certificate limit, failed validations). Prefer restoring the saved certificate bundle. When testing a fresh issuance, use the ACME staging issuer (`acme_staging: true`). Switch to production once, after DNS points at the new host.

## 3. Supabase pause/wake check

Supabase Free pauses projects after about 7 days of low activity.

1. A keep-alive `SELECT 1` runs every 6 hours; confirm its last success:
   ```sql
   -- psql "$SUPABASE_OPS_URL"   (session pooler, port 5432)
   SELECT max(finished_at) AS last_success, now() - max(finished_at) AS age FROM ops.keepalive_runs WHERE succeeded;
   ```
   or in Prometheus: `time() - cdr_supabase_keepalive_last_success_timestamp` (alert `CdrSupabaseKeepaliveStale` above 12 h = 43200 s). An empty result means no success is recorded, not a recent one.
2. Do a pre-demo wake check at T-24h and T-1h: connect through the session pooler (port 5432), run `psql "$SUPABASE_OPS_URL" -c 'SELECT 1'`, then check readiness: `curl -fsS https://ops.cproj.obed.one/api/overview` (integration `supabase` is `fresh`) and the relief `/health/ready` through the load balancer (`curl -fsS https://app.cproj.obed.one/health/ready`).
3. To run the keep-alive now instead of waiting: `st2 run cdr.keepalive`, then repeat the query in step 1; a failed run is recorded with a short error class in `ops.keepalive_runs.detail`.
4. If paused, restore the project from the Supabase dashboard, wait until it is active, then rerun steps 2 and 3.
5. Confirm database size is under the 400 MB alert threshold: `psql "$SUPABASE_OPS_URL" -c "SELECT pg_size_pretty(pg_database_size(current_database()))"` or `cdr_supabase_db_size_bytes` in Prometheus.

## 4. Live restore rollback

Applies to the schema-swap restore in docs/09. The live schema was renamed to `app_prev` before import.

1. If the restore transaction fails, it rolls back as a whole; verify `app` is still the original schema.
2. If the restore committed but API verification fails:
   ```sql
   SET lock_timeout = '5s';
   BEGIN;
   ALTER SCHEMA app RENAME TO app_failed;
   ALTER SCHEMA app_prev RENAME TO app;
   COMMIT;
   ```
3. Check `ops.runtime_flags` hash against the pre-restore value, run API verification, then clear maintenance.
4. Keep `app_failed` for diagnosis, then drop it. Drop `app_prev` only after a restore is verified (docs/09).

## 5. First bootstrap

*Run once as BOOTSTRAP-1 (evidence: `drproj-mgmt/tests/evidence/BOOTSTRAP-1.md`).* Use when the four pre-created Debian 12 hosts (management 103.141.79.170, primary .171, load balancer .172, testing .173) have never been configured. **Approved deviation: the fixed hosts are operated as `root`; there is no `drproj` deploy user on them** (docs/07, `bootstrap`). Ansible runs from the management VM, not the workstation.

1. **Key check.** From the workstation: `ssh -i ~/.ssh/drproj root@<ip> hostname` for all four. Fingerprints: compare `ssh-keyscan -t ed25519 <ip> | ssh-keygen -lf -` with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the host (provider console or an already trusted session), then add to `known_hosts`. Host key checking stays on; never use `StrictHostKeyChecking=no`. If a host key changes unexpectedly, stop and confirm before accepting it.
2. **Controller on management.** `apt install python3-venv python3-pip rsync`; `python3 -m venv /opt/drproj/ansible-venv`; `pip install ansible-core==<versions.ansible>`; `ansible-galaxy collection install -p /opt/drproj/ansible-venv/collections -r requirements.yml` (the bootstrap role's `ansible_venv` tag keeps this reproducible). Sync `infra/ansible/` to `/opt/drproj/ansible/` with rsync (exclude `__pycache__`; do not `--delete` over `deployment-inputs.local.json`, which sits in that directory at mode 0600). Management uses the same key as the workstation, `/root/.ssh/drproj` (mode 0600, copied over SSH by the operator's decision; it is already authorized for root on all four hosts). Pin the other hosts' host keys in management's `known_hosts` (same fingerprint comparison).
3. **Inputs and inventory.** On management: `scripts/gen-inventory.py --inputs deployment-inputs.local.json --bootstrap --local-role management --output inventory/generated.yml` (management reaches itself with `ansible_connection=local`). Run playbooks with `-i inventory/generated.yml` (`inputs_file` defaults to `/opt/drproj/ansible/deployment-inputs.local.json` on management through `group_vars/management.yml`; pass `-e inputs_file=...` only to override) and `ANSIBLE_COLLECTIONS_PATH=/opt/drproj/ansible-venv/collections`. The node_exporter password is generated on the host; nothing is exported.
4. **Run order.** `bootstrap.yml --limit management --check --diff` (Docker packages cannot be resolved in check mode until the repository exists), then for real; then the same for `primary`, `lb`, `testing`. Stop a host on its first failure; do not retry blindly.
5. **Verification.** On each host: `docker --version` (matches `versions.docker`), `stat -c "%a %n" /etc/drproj /etc/drproj/secrets` (700). On management also `swapon --show` (2 GB) and `sysctl vm.swappiness` (10), then `BACKUP_DIR=... TFSTATE_DIR=... scripts/check-perms.sh`. On primary the web config exists and `/etc/drproj/secrets/node-exporter-basic-auth-password` is 0600. Root login stays enabled (`bootstrap_disable_password_auth` is false until you opt in).
6. **Rollback.** The bootstrap changes nothing outside the host. Undo: `apt-mark unhold docker-ce docker-ce-cli`, `swapoff /swapfile` and remove its `/etc/fstab` line (management), remove `/etc/drproj`, remove `/root/.ssh/drproj` from management if the key should no longer live there. Re-running the playbooks is safe (idempotent).
7. **CA rotation** (after the first bootstrap, when the CA must change): `ca-rotate-bundle.yml`, confirm every verifier accepts both CAs, `ca-rotate-reissue.yml`, confirm probes are healthy, then `ca-rotate-drop-old.yml -e ca_confirm_drop_old=true`; the old key stays in `/etc/drproj/ca/retired/` until deleted by hand.
8. **Cloud VM first contact (user switch).** `app.yml` bootstraps cloud VMs in its own first play, connecting as the Terraform-provisioned user (`cloud_first_contact_user`, default `root`, override `CLOUD_VM_SSH_USER`) with the cloud key (`CLOUD_VM_SSH_KEY_FILE`). That play creates `drproj` (cloud VMs only; fixed hosts stay root), copies the first-contact user's `authorized_keys` to it (Terraform already installed the key, so no `.pub` file is read or mounted), and does not harden sshd (`bootstrap_harden_sshd=false` in `group_vars/cloud_app.yml`). Every later play connects as `drproj` with the same key. Host key policy is `accept-new` for cloud VMs only. Fixed hosts use the root-mode flow above (controller-side `.pub`, no user switch, no sshd hardening).

## 6. Supabase init

*Run once as DB-1 (evidence: `drproj-mgmt/tests/evidence/DB-1.md`).* Needs the Supabase session-pooler string for the `postgres` user and an SSH session on management. The password goes only into `/etc/drproj/secrets/*.env` (0600) and the shell environment.

1. **Tooling.** Management has Docker but no Node or psql, so run both from containers: `postgres:17-alpine` for `psql`, `node:22-bookworm-slim` for the repos synced to `/opt/drproj/src/{drproj-app,drproj-mgmt}` (`pnpm install --frozen-lockfile`; drproj-mgmt links `../drproj-app`, so mount the parent directory). Download Supabase's root CA, compare its SHA-256 with the root the pooler presents (`openssl s_client -starttls postgres`), install it as `/etc/drproj/ca/supabase-root-2021.crt` and pass `NODE_EXTRA_CA_CERTS`. Without it Node fails with "self-signed certificate in certificate chain". Never set `NODE_TLS_REJECT_UNAUTHORIZED=0`.
2. **Server version.** `SELECT version()` through the pooler; record the major as `supabase.postgres_server_major` (17).
3. **Migrate first, then logins.** The `dr_*` group roles are created by `ops/0001` (and `dr_automation` by `ops/0009`), so run `pnpm migrate` with the `postgres` URL (`db-migration.env`) before creating login users: 13 ops + 2 app migrations; a second run prints "No pending migrations."
4. **Login users.** Per role: a 32-character alphanumeric password from `openssl rand`, `CREATE ROLE <r> LOGIN PASSWORD ...` then `GRANT dr_<r> TO <r>` (`app_runtime`→`dr_app_runtime`, `ops`→`dr_ops`, `backup`→`dr_backup`, `restore`→`dr_restore`, `automation`→`dr_automation`), and `db-<r>.env` holding `SUPABASE_APP_URL`, `SUPABASE_OPS_URL`, `SUPABASE_BACKUP_URL`, `SUPABASE_RESTORE_URL` or `DR_AUTOMATION_URL`. Supply the password through stdin, not argv. Re-running keeps existing passwords.
5. **Seed.** `pnpm seed:policy` (drproj-mgmt, ops URL) creates policy version 1 active; `pnpm seed` (drproj-app, app URL) inserts the 12 demo requests when `DEMO_MODE` is intended. Both are idempotent.
6. **Verify per role** (rolled-back attempts): `app_runtime` reads/writes `app.*`, executes `ops.app_admit_write()` and is denied everything else in `ops`; `ops` has no write path to capacity tables or `app`; `automation` reads capacity tables and calls capacity functions (it has no direct table writes by design) and is denied `app`; `backup` is read-only on `app` and denied `ops`; `restore` can create objects in and rename schema `app` and is denied `ops`.
7. **Keep-alive and size.** Run the `cdr.keepalive` logic once against the ops URL (a `stackstorm` row in `ops.keepalive_runs`) and check `pg_database_size` against the 450 MB restore budget.
8. **Rollback.** Drop the login roles (`DROP ROLE`) and delete their env files; drop schemas `app`/`ops` only on an empty project.

## 7. Deploy primary

*Run once as DEPLOY-PRIMARY (evidence: `drproj-mgmt/tests/evidence/DEPLOY-PRIMARY.md`).* Needs bootstrap, CA and Supabase init done, the image digest in `deployment-inputs.local.json` (`registry.application_image_digest`, `allow_local_tag=false`), and an SSH session on management.

1. **Sync.** Put the current `infra/ansible/` in `/opt/drproj/ansible/` (as in "First bootstrap") and the migration files in `/opt/drproj/src/drproj-app`. Apply new ops migrations first (`pnpm migrate` with `db-migration.env`, via `/opt/drproj/scripts/dbrun.sh`): 0014 adds `ops.register_fixed_instance`.
2. **Environment.** In the shell only, never in a file under the repos: `set -a; . /etc/drproj/secrets/db-app_runtime.env; . /etc/drproj/secrets/db-automation.env; set +a; export BACKEND_CA_BUNDLE=/etc/drproj/ca/ca-bundle.crt`. The role renders `/etc/drproj/app/.env` (0600) on primary from it and copies the Supabase root CA to `/etc/drproj/app/ca/` (mounted for `NODE_EXTRA_CA_CERTS`). `group_vars/primary.yml` sets `instance_id: primary` and `app_demo_mode: true`.
3. **Run.** `ansible-playbook playbooks/app.yml --check --diff --limit primary-vm -e inputs_file=/opt/drproj/ansible/deployment-inputs.local.json` (check mode stops at "Start the stack": the compose file does not exist yet), then without `--check`. The play re-issues the primary leaf with CN `primary` and ends with its own liveness, readiness, identity and digest checks. If health fails, read `docker logs drproj-app-relief-1` once and stop; do not loop restarts.
4. **Verify from management** with `--cacert /etc/drproj/ca/ca-bundle.crt`: `/health/live` and `/health/ready` return 200 (`maintenance_generation`, `X-Instance-Id: primary`, `X-App-Version`); `docker inspect` image and `RepoDigests` equal the digest; the container runs as `node` (uid 1000); `GET /api/requests` lists the seeded rows; a `POST` repeated with the same `Idempotency-Key` returns 201 then 201 with `Idempotent-Replayed: true`; `PATCH` with `expected_version: 1` returns version 2; `https://<ip>:9100/metrics` is 401 without and 200 with the node_exporter password; `http://<ip>:9464/metrics` is 200.
5. **Register.** Call `ops.register_fixed_instance('primary', '<public ip>', '<digest>')` as the `automation` login (idempotent; routing stays `none`, `simulated=false`). Confirm the row as `ops`.
6. **Rollback.** `docker compose -p drproj-app down` in `/etc/drproj/app` on primary; set the instance `lifecycle_state` back by re-running the function after a redeploy, or delete the row as the migration login. Re-running `app.yml` is safe.

## 8. Management stack bring-up

*Run once as MGMT-STACK (evidence: `drproj-mgmt/tests/evidence/MGMT-STACK.md`).* Needs bootstrap, CA and Supabase init done, DNS for the four ingress hostnames pointing at management, `ingress.acme_email` set in `deployment-inputs.local.json`, and the operations and runner image digests in the inputs. **Provider tokens are not needed and are not installed: the real adapter is configured but provisioning is impossible until they exist.**

1. **Sync.** From the workstation: `rsync -a` the drproj-mgmt checkout to `/opt/drproj/src/drproj-mgmt/` (exclude `.git`, `node_modules`, `deploy/inputs/*.local.json`, `.env*`, `secrets/`, `monitoring/targets/*/*.json`) and `infra/ansible/` to `/opt/drproj/ansible/` (exclude `deployment-inputs.local.json`, `inventory/generated.yml`). The Compose files mount repo-relative paths (`automation/`, `monitoring/`), so the whole tree must be there.
2. **Database login for the pack.** `scripts/db-init/create-stackstorm-login.sh` (migration login, `db-migration.env`) creates the `stackstorm` login as a member of `dr_ops` and `dr_automation` and writes `/etc/drproj/secrets/db-stackstorm.env` (0600). The pack needs one login in both groups; with the separate `automation` login `cdr.keepalive` and `cdr.alert_intent` fail with `record_failed:privilege`.
3. **Preflight and run.** On management: `cd /opt/drproj/ansible; export PATH=/opt/drproj/ansible-venv/bin:$PATH ANSIBLE_COLLECTIONS_PATH=/opt/drproj/ansible-venv/collections`, then `ansible-playbook playbooks/mgmt.yml --check --diff --limit management-vm` (stops at "Validate the rendered Compose project": the env file does not exist before the first real run) and without `--check`. The role checks that each hostname resolves to management (`getent`; `dig` is not installed), that 80/443 are free, that the DB env files are 0600, and it never prints a secret (`no_log`). It generates and keeps in `/etc/drproj/mgmt/` (0700): the operator password (`/etc/drproj/secrets/ops-operator.pw`) and its bcrypt hash, the Grafana admin and `st2admin` passwords and the st2 htpasswd, Mongo/RabbitMQ passwords, the pack config (`provider_adapter: real`, `REPLACE_ME_BEFORE_PROVISIONING` for region/size/image, no tokens), and the node_exporter scrape password, read once from the primary. Files read inside containers are owned by the container user (st2 uid 103, Grafana 472, nobody 65534); `st2_api_key` is the one 0640 file (Alertmanager owner, st2 group), inside the 0700 directory. Read the operator password with `! cat /etc/drproj/secrets/ops-operator.pw`.
4. **Certificates.** Keep `ingress.acme_staging: true` for the first run and check each host with `echo | openssl s_client -connect <host>:443 -servername <host> | openssl x509 -noout -issuer` (expect `(STAGING)`). Then set it to `false` in the inputs, rerun the play (Caddy is recreated and fetches production certificates; allow about a minute) and check the issuer again. Let's Encrypt limits duplicate certificates, so do not flip back and forth.
5. **Two-phase StackStorm start.** The play starts everything, registers the pack (`st2-register`, which validates the pack config against its schema), builds the pack virtualenv, creates the `St2-Api-Key` once (`/etc/drproj/mgmt/st2-cli.sh apikey create`), re-renders the environment and recreates `ops` and `alertmanager`, then disables `cdr.reconcile_every_60s`. The 6 h `cdr.keepalive_every_6h` rule stays enabled. **The 60 s reconcile stays disabled until provider tokens exist**; enable it with `/etc/drproj/mgmt/st2-cli.sh rule enable cdr.reconcile_every_60s` only after a manual `cdr.reconcile` ends `succeeded` in `ops.operations`.
6. **Verify.** `https://ops.<domain>/api/overview` is 200 with `supabase`, `prometheus` and `stackstorm` `fresh`, `haproxy` "not probed in this phase" and `providers` `unavailable`; `GET /api/instances` lists the primary with `simulated=false`; POST/PATCH/PUT/DELETE without credentials return 401 with `WWW-Authenticate`, with `operator:<password>` they do not; Grafana redirects to its login and `/api/search` is 401; the StackStorm API is 401 without a token; ports 9090 and 9093 are refused from outside (no published ports); Prometheus targets `relief` (:9464, HTTP) and `node` (:9100, HTTPS with the project CA and basic auth) are up; `/etc/drproj/mgmt/st2-cli.sh run cdr.keepalive` adds an `ops.keepalive_runs` row. `docker stats --no-stream` gives the T18 snapshot.
7. **Known state without tokens.** A provider whose token variable is unset is *unconfigured*: the real adapter makes no API call and answers `unavailable` with code `provider_unconfigured` (never an exception, never absence). `cdr.reconcile` skips that provider's tag scan and opens ONE warning incident `provider.unconfigured:<provider>` (fingerprint-deduped, no new event per tick). With both providers unconfigured the operation ends `succeeded` with `noop_reason=providers_unconfigured`; with one unconfigured the other is scanned as usual. Both incidents are visible in `/api/incidents` and stay open until an operator resolves them once the token exists. `/api/overview` keeps `providers` `unavailable`.
8. **Rollback.** `docker compose --project-name drproj-mgmt --env-file /etc/drproj/mgmt/mgmt.env -f compose.yaml -f compose.ansible.yaml down` in `deploy/compose/mgmt` (add `-v` only to discard StackStorm, Prometheus and Grafana data). Re-running the play is safe. To rotate the operator password, delete `ops-operator.pw` and `ops_basic_auth_hash` and rerun.

## 9. Load balancer bring-up (P6.1)

*Run once as P6.1 (evidence: `drproj-mgmt/tests/evidence/P6.1-LB.md`).* Needs bootstrap, CA and the management stack done, DNS `app.cproj` pointing at the LB (TTL 60 s), `ingress.acme_email` set, `hosts.load_balancer.public_ip` and `rate_limit_exempt_ips` in the inputs.

1. **Sync** the drproj-mgmt checkout and `infra/ansible/` to the management VM as in section 8, step 1 (the role vendors the Compose/HAProxy files from `drproj-app`; `tests/test_lb_parity.py` keeps them identical).
2. **Dry run and apply.** On management: `ansible-playbook -i inventory/generated.yml playbooks/lb.yml --check --diff --limit load_balancer-vm -e inputs_file=...`, then without `--check` and with `-e lb_acme_staging=true` (the inputs hold `ingress.acme_staging`; the staging run proves the account, the compose wiring and the deploy hook). The role: renders `.env` (0600) with the stats password (created on first use in `/etc/drproj/mgmt/secrets/haproxy_stats_password`), the exempt-IP list and the CA bundle; issues the LB's admin leaf (key generated on the LB, CSR signed on management, SAN = LB IP); creates a placeholder `app.pem`; validates the config with `haproxy -c` in a one-shot container; starts the stack; issues and deploys the app certificate; verifies issuer, liveness and headers from the controller; takes the backup.
3. **Production certificate.** After the staging run succeeds and DNS is right, run once with `-e lb_acme_staging=false`: the role force-reissues from Let's Encrypt production over HTTP-01 and hot-updates the pem (no restart). Do not alternate staging and production (rate limits).
4. **Management side.** Rerun `playbooks/mgmt.yml`: it generates the runner's mTLS client certificate, writes the Prometheus targets (`haproxy` on `<LB>:8404`, `app_cert` for the public URL), points Caddy's `stats` site at `<LB>:8404`, and adds `haproxy` to `PROMETHEUS_EXPECTED_JOBS`. `/api/overview` then reports haproxy `fresh` once the operations image with the probe (P6.1 or later) is deployed.
5. **Reload after a config change.** Edit `drproj-app/deploy/compose/lb`, copy to `roles/lb/files/`, run the role: it validates, dumps `show servers state`, and reloads gracefully. Change of the primary address: delete `state/server-state` on the LB first.
6. **Checks.** `curl -sD- https://app.cproj.obed.one/health/live` (200, `X-Request-ID`, HSTS, nosniff, Referrer-Policy, CSP); `https://<LB>:8404/stats` and `/metrics` are 401 without credentials; port 9999 refuses a client without a project-CA certificate (TLS alert) and answers `show info` for the runner certificate; Prometheus targets `haproxy` and `app_cert` are up; `probe_ssl_earliest_cert_expiry` is present.
7. **Rollback.** `docker compose -p drproj-lb down` in `/opt/drproj/lb` (volumes keep the acme account); restore from the section 2 backup.
