# Containers, public networking, DNS, and TLS

## Per-role Compose projects

| Role | Services and persistence |
|---|---|
| Management | Caddy, ops frontend/API, StackStorm services with MongoDB, RabbitMQ and Redis (the coordination backend, docs/07), automation runner(s), Prometheus, Alertmanager, Grafana, restore-test PostgreSQL container as needed |
| Load balancer | HAProxy; acme.sh ACME client/challenge helper; persistent routing config, certificates, server-state material |
| Application | Same pinned relief image on primary, DigitalOcean and Linode; runtime config and backend TLS certificate mounts; no local business state |
| Testing | k6 jobs and result artifacts |

All long-running services have appropriate restart policies. Health checks report startup/readiness separately. Docker marking a container unhealthy is not itself a complete automated VM recovery implementation.

Use persistent volumes/bind mounts for native databases, workflow state, Terraform state, pg_dump files, monitoring history, TLS material, and durable configuration. Persistent volumes do not equal independently backed-up infrastructure.

The management VM is 8 vCPU/16 GB with a 62 GB disk (real size; the first plan assumed 2 vCPU/4 GB, so the limits below stay conservative until measured). Start with one infrastructure job at a time, low Ansible forks, short monitoring retention, bounded Node pools, and measured dependency usage. Validate headroom with a complete stack plus Terraform/restore job. Memory controls: set a per-container `mem_limit`, run one heavy job at a time (`resource_class=heavy`, concurrency 1), do not run the restore-test concurrently with capacity operations, configure a 2 GB swapfile with `vm.swappiness=10`, and run one StackStorm actionrunner. More slots (up to 5 per provider) mean more Terraform/Ansible runs over time, but capacity changes stay one mutation at a time. Measurement is still required. Do not silently resize the user's VM or promise the entire stack fits without measuring. Pause or serialize non-critical work during memory pressure; report inability to meet the selected baseline honestly.

## Public network

All VMs have public IPs. No VPN, firewall setup, cloud firewall resources, UFW, iptables, or restrictive security-group automation is part of the user's scope.

Applications and management communicate using host public IPs and published Docker ports. Docker service names resolve within their Compose network, not between VMs.

Do not publish native database/queue ports merely to connect containers on the same management host: Compose internal networking suffices. This is container topology, not firewall automation.

Ansible uses SSH keys. Supabase uses TLS. Encrypt application/metrics traffic between VMs using project-controlled backend certificates with an explicit trust bundle where public certificates do not cover the backend identity. Generate instance identities/certificates during controlled deployment and configure verification. Public-IP reachability does not require turning off TLS verification.

## DNS in Vercel

Zone: obed.one. Each A-record name is relative to that zone.

| Record | Value |
|---|---|
| app.cproj | Load-balancer public IPv4 |
| ops.cproj | Management public IPv4 |
| grafana.cproj | Management public IPv4 |
| stackstorm.cproj | Management public IPv4 |
| stats.cproj | Management public IPv4 |

Use actual IPv6 AAAA records only when the corresponding service is reachable over IPv6. Do not add fictional records. Vercel manages DNS; it does not terminate TLS for traffic sent directly to these VMs.

## HAProxy host

Publish HTTPS 443 for app traffic and HTTP 80 for redirects/ACME HTTP validation. HAProxy balances only the relief application. Its statistics/metrics listener is a separate administrative endpoint. The management Caddy proxy reaches that listener for stats.cproj.obed.one.

Initial configuration: primary active, healthy DigitalOcean eligible as backup, Linode absent. Activate DigitalOcean for overflow by explicit routing mode/weight change. A backup flag alone handles failure, not CPU-based overflow.

Keep applied backend membership/drain state durable and validated before reload. Expose request rate, connections, backend health, error rates, and versioned membership. Do not retry non-idempotent POSTs blindly at the proxy; application idempotency handles caller retries.

### HAProxy configuration details

- Health check: `option httpchk` against `/health/live` only (no database, cheap, mounted first), over TLS with the backend trust bundle (`ssl` on the server line already makes checks use TLS; `check-ssl` would be redundant and is not used). `/health/ready` (database/schema) is for dashboard, Prometheus, and StackStorm admission and never removes a backend, so a Supabase outage does not take backends out.
- Primary server line: `server primary <ip>:8443 check inter 10s fall 3 rise 6 ssl verify required ca-file <trust bundle> sni str(<app host>) check-sni <app host> verifyhost <app host>` (detection about 30 s; 6 successes enforces about 60 s healthy before return). Warm standby and elastic servers use the same timings with `rise 2`. `timeout check 4s` is not a server-line argument; set it in the `backend` (or `defaults`) section. These mirror policy `health.consecutive_failures` 3, `consecutive_successes` 6, `timeout_seconds` 4.

```
backend be_app
    option httpchk GET /health/live
    timeout check 4s
    server primary <ip>:8443 check inter 10s fall 3 rise 6 ssl verify required ca-file <bundle> sni str(<app host>) check-sni <app host> verifyhost <app host>
    server warm    <ip>:8443 check inter 10s fall 3 rise 2 ssl verify required ca-file <bundle> sni str(<app host>) check-sni <app host> verifyhost <app host> backup
```

**As built (P6.1; source `drproj-app/deploy/compose/lb/haproxy/haproxy.cfg`, deployed by the `lb` role).**

- *Server lines.* `primary` (address from the deployment inputs, `PRIMARY_ADDR`), then warm and elastic lines pre-declared and `disabled` (`addr 0.0.0.0`): `warm_bk` (`backup`, always ready as the failure target), `warm_act` (non-backup, `maint` in normal mode) and, with the same pairing for an interim warm standby, `iwarm_bk` (`backup`) and `iwarm_act` (non-backup, `maint`, `rise 2`), both declared in `haproxy.cfg`; `elastic-do-1..5` and `elastic-linode-1..5`. A `backup` flag cannot be toggled at runtime, so every routing mode is runtime-only: normal = `warm_act` maint; overflow = `warm_act` `ready` with its weight set; ramp = primary weight set first, then `ready`. StackStorm fills an elastic line with `set server addr <ip> port 8443` + `state ready`; `add server` is not used because the server-state file does not persist dynamic servers.
- *Checks.* The health-check SNI and hostname verification use the application name (`sni`, `check-sni`, `verifyhost`), because the backend leaf carries `IP:<host>,DNS:<app host>` and checks otherwise fail with "different certificate". `verify required` was exercised: a name mismatch kept the primary `DOWN` (L6RSP) until SNI/verifyhost were set.
- *Environment in the config.* `PRIMARY_ADDR`, `APP_HOST`, `HAPROXY_STATS_USER`, `HAPROXY_STATS_PASSWORD` come from `.env` (0600) rendered by the `lb` role; in the config they must be double-quoted (`sni str("${APP_HOST}")`): unquoted inside `str()` they are not expanded.
- *Config mounting.* The whole `./haproxy` directory is mounted (`/usr/local/etc/haproxy/conf`: `haproxy.cfg`, `errors/503.http`, `trusted_test_ips.lst`, `admin_allow.regex`, `ca-bundle.crt`; no secrets, mode 0755 because HAProxy runs as uid 99). Single-file bind mounts keep the old inode after Ansible replaces a file by rename, so a reload would re-read stale content.
- *Server-state file.* `load-server-state-from-file global` for `be_app`; `none` for `be_acme` (the file restores container addresses, which Compose reassigns on recreate). Changing the primary address means deleting `state/server-state` before the reload. The `lb` role dumps `show servers state` before every reload and reloads gracefully (`SIGUSR2`, master-worker) after `haproxy -c` in a one-shot container.
- *Runtime API.* `stats socket ... ssl` is accepted by `haproxy -c` but ignored: the TCP socket stays plaintext (found on the real host and closed within minutes). The remote Runtime API is `fe_admin` (`mode tcp`, `bind :9999 ssl ... ca-file <project CA bundle> verify required`) in front of the local `admin.sock`, with one allow-listed command per connection (`admin_allow.regex`: `show info|stat`, `show servers state|conn`, `set server be_app/<name> state ready|drain|maint | weight N[%]` for any server including `primary`, and `addr <ipv4> port N` only for `warm_bk|warm_act|iwarm_bk|iwarm_act|elastic-{do,linode}-1..5`, never `primary`; the whole payload is anchored, so `;`-chained or other commands are rejected). The runner's client certificate (CN `drproj-runner`, `clientAuth`) is issued by the project CA on the management VM (`mgmt` role, `runner-client.{crt,key}` 0600, mounted into `st2actionrunner`). The local `admin.sock` (mode 660, uid 99, shared with the acme container) is not published. `deploy/compose/lb/check_config.py` (CI job `haproxy-config` and the `lb` role before `haproxy -c`) fails the config if any `stats socket` is not a unix socket, any bind is outside 80/443/8404/9999, or any TLS bind other than 443 and 8404 lacks `verify required`. The fallback (SSH forced command) is not needed.
- *Stats and exporter.* `fe_stats` on 8404: TLS with the project-CA leaf (SAN = LB IP), `http_auth` against the `prometheus` user (password in `/etc/drproj/mgmt/secrets/haproxy_stats_password`, also used by the Prometheus job), `/stats` and `/metrics` (built-in `prometheus-exporter`). Caddy proxies `stats.<domain>` to it and verifies the leaf against the same CA bundle (`HAPROXY_STATS_CA`).
- *429 responses* are generated before the response rules run, so they carry `Retry-After: 10` but not the security headers or `X-Request-ID`.
- Do not use `observe layer7 on-error mark-down`. Never use `retry-on 503`.
- Stats socket at `level admin` so `apply_routing` can use the Runtime API. Persist state with `server-state-file` and `load-server-state-from-file global`; dump `show servers state` after every runtime change and before reload.
- Maint latch: on primary failure, `apply_routing` runs `set server be_app/primary state maint` so a recovered primary stays out until failback sets it `ready`.
- Failback mechanics: `maint` disables HAProxy health checks for that server, so HAProxy cannot see the primary recover. While the primary is in maint, StackStorm probes primary `/health/live` and `/health/ready` directly from the management VM (port 8443). Sequence: (a) direct probes healthy for 60 s continuous; (b) `set weight be_app/primary 0` and ensure ramp-mode config (cloud servers non-backup) before leaving maint; (c) `set server be_app/primary state ready`, which resumes HAProxy checks while the primary receives no traffic at weight 0; (d) wait for HAProxy to report UP (rise 6, about 60 s); (e) set weight to the 10% equivalent, then 50%, then 100%, observing 60/60/120 s with the step gates; (f) return to normal mode (warm = backup). Weight is always set before traffic is admitted. Gating is in docs/03 and docs/04.
- `errorfile 503` provides the response when no backend is available.
- Rate limits: stick-tables per source IP return 429 with `Retry-After`: about 50 requests/10 s general, 20 POST/10 s, demo workload 5/10 s. Request body limit 8 KB. No CAPTCHA.
- Test-host exemption: only untrusted sources are tracked, e.g. `http-request track-sc0 src unless { src -f /etc/haproxy/trusted_test_ips.lst }`. Ansible generates the list from the testing host IP in deployment inputs (`rate_limit_exempt_ips`). The app skips its own per-IP limits for that IP too. k6's own caps (200 rps, 600 s) and the app's bounded worker pool still apply; all other IPs keep the public limits.
- `unique-id-format` / `unique-id-header X-Request-ID` so each request carries an ID through logs.
- Response headers: HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`. No CORS headers by default.

## Management host

Caddy publishes 80/443 and routes management hostnames directly to local container services:
- ops: frontend plus /api to management API.
- grafana: Grafana UI.
- stackstorm: native StackStorm UI/API integration.
- stats: reverse proxy to the HAProxy admin statistics endpoint.

The relief application has no login. The ops dashboard is readable without login, but mutating methods on `ops.cproj.obed.one` (POST, PATCH, PUT, DELETE) are protected by Caddy `basic_auth` with one shared operator password. Native tool/API credentials stay in backend integration configuration. Do not add custom RBAC or Supabase Auth.

```
@mutating method POST PATCH PUT DELETE
basic_auth @mutating {
    operator {env.OPS_BASIC_AUTH_HASH}
}
```

Generate the bcrypt hash with `caddy hash-password` and supply it from the environment. Unauthenticated mutations return 401. The audit actor is recorded as `operator`; there is no per-user audit.

### Admin UI hardening

- Grafana: anonymous access off, sign-up off, admin password via `GF_SECURITY_ADMIN_PASSWORD__FILE`.
- StackStorm: keep authentication enabled, change the `st2admin` password, and use an `St2-Api-Key` for Alertmanager webhooks.
- HAProxy stats: `stats auth` over TLS on the 8404 listener.
- Prometheus and Alertmanager are not published; reachable on the Compose network only.
- node_exporter on 9100 uses `--web.config.file` with TLS and basic auth.

## Certificates

Use individual public hostname certificates by default. Caddy automatically issues/renews its management certificates. On the HAProxy host, an acme.sh container obtains/renews app.cproj.obed.one through HTTP validation and deploys the renewed certificate through a tested HAProxy update/reload hook.

Configure HTTP challenge routing without interrupting normal HTTPS service. Certificates/account data persist outside disposable containers. Renewal failure produces an incident before expiry. Use an ACME staging issuer while validating automation.

ACME as built (P6.1): standalone HTTP-01 on 8888 inside the `acme` container (HAProxy `fe_http` sends `/.well-known/acme-challenge/` to `be_acme`), `--keylength ec-256`, deploy hook `haproxy` with `DEPLOY_HAPROXY_HOT_UPDATE=yes` over the shared admin socket (same pem path in both containers, `DEPLOY_HAPROXY_RELOAD` chowns the pem to uid 99, mode 0600), so renewal needs no restart. The container's cron runs `acme.sh --cron` at 04/10/16/22:48 and redeploys through the stored hook. First boot uses a 7-day self-signed placeholder. Staging authorizations are cached by Let's Encrypt for 30 days, so a staging run proves account and deploy hook but not the challenge path; the first production run is the real test (a stale server-state address for `be_acme` caused a 503 there until `load-server-state-from-file none` was set). The `lb` role decides staging versus production from the issuer of the certificate acme.sh holds, not from its config.

Backend CA: the CA private key lives on the management VM at `/etc/drproj/ca/ca.key` with mode 0400. Leaf certificates are valid 90 days and reissued by Ansible; rotating the CA follows a fixed order: (1) create the new CA and build a bundle holding old and new; (2) deploy that bundle to every verifier (HAProxy, Prometheus/blackbox_exporter, and the management stack) and confirm they accept both; (3) reissue every leaf from the new CA and redeploy them; (4) after all leaves and probes are healthy on the new CA, drop the old CA from the bundle, redeploy, and retire the old key. Never replace the bundle with the new CA alone before step 3 completes. The plays are `ca-rotate-bundle.yml` (1-2), `ca-rotate-reissue.yml` (3) and `ca-rotate-drop-old.yml` (4, needs `-e ca_confirm_drop_old=true`); docs/07 describes them. A `blackbox_exporter` probe alerts when `probe_ssl_earliest_cert_expiry` is under 14 days. Leaf key modes: `app.key` on application hosts is 0640 root:1000 (the `node` group of the relief image; node_exporter joins it with `group_add`) in the 0700 directory; the LB's `lb-admin.key` and the staged leaf keys are created under `umask 077` (0600).

This approach does not require Vercel DNS API credentials. Wildcard issuance is not required by the current hostname set. If later selected, it is a deliberate DNS-validation change.

## Operational ports

Exact port mappings belong in deployment config. Defaults: public 80/443 on LB and management, SSH 22 on VM hosts, application HTTPS 8443, app diagnostics/metrics via the configured app listener, host metrics HTTPS 9100, HAProxy admin statistics HTTPS 8404.

Secrets live in `.env` files with mode 0600 inside owner-only directories (0700) on the management VM, passed to containers as Docker Compose environment or secrets. They are plaintext at rest (accepted risk, docs/12). Deployment inputs reference them as `env://NAME`.

The management VM's other owner-only directories are the backup directory (`BACKUP_DIR`, `backup.dir` in the deployment inputs, default `/var/backups/drproj`) and the Terraform state root (`TFSTATE_DIR`, `cloud_application.terraform.tf_state_dir`, default `/var/lib/drproj/terraform`); both are mode 0700 with files 0600. The `bootstrap` role (tag `perms_check`) creates them and installs `scripts/check-perms.sh` as `/opt/drproj/scripts/check-perms.sh`; verify with `BACKUP_DIR=/var/backups/drproj TFSTATE_DIR=/var/lib/drproj/terraform /opt/drproj/scripts/check-perms.sh` (exit 1 on any deviation).

Direct-IP access to the application VMs is not blocked (accepted risk, docs/12); maintenance and rate-limit rules are enforced in the app as well as at HAProxy.

## Resolved images, tags and ports (Phase 3.1)

Compose files: `drproj-mgmt/deploy/compose/{mgmt,testing}`, `drproj-app/deploy/compose/{app,lb}`. Every service sets `mem_limit`, a healthcheck and a pinned tag; only stateful services have named volumes. The tags are the latest stable at 2026-10-24 (`config/deployment-inputs.example.json`, `versions.*`) and are **unverified until smoke**: a tag counts as verified only after the stack has run on the target VM. The mongo, rabbitmq and redis tags follow what the StackStorm 3.9 images are used with rather than the newest releases. The `mem_limit` values (management stack always-on services 3696 MB in total; the real VM has 16 GB, so that is about 23% of RAM; st2-register and restore-test-pg are profile-gated and excluded) were measured once on the VM at idle (MGMT-STACK, T18 snapshot): the 20 containers used about 1.6 GB in total. Grafana sat at 252 of its 256 MB ceiling, so its ceiling is now 384 MB (MGMT-STACK-FIX); the st2 services use 67-91 MB of 160-192 MB. The one-heavy-job part of T18 was measured once (MGMT-STACK-FIX: `cdr.restore_probe` beside the full stack, peak about 1.9 GB summed over containers, no OOM, swap unchanged) on a 10 KB app schema; repeat it with realistic data in P7.

| Component | Image and tag |
|---|---|
| Caddy | `caddy:2.11.7` |
| ops API/dashboard, relief app | `drproj-operations`, `drproj-relief` built from the repos (Node `22.23.4-alpine`, non-root); local tag `:local` until a published digest exists (`registry.*_image_digest`) |
| StackStorm | `stackstorm/st2*:3.9.0` (one `st2actionrunner`) |
| MongoDB / RabbitMQ / Redis | `mongo:7.0.45`, `rabbitmq:4.3.7`, `redis:7.4.11-alpine` |
| Prometheus / Alertmanager / blackbox_exporter | `v3.15.1` / `v0.34.1` / `v0.29.1` |
| Grafana | `13.2.4` |
| node_exporter | `v1.12.1` |
| restore-test and local PostgreSQL | `postgres:17.11-alpine` (use the Supabase `postgres_server_major` once it is known) |
| HAProxy / acme.sh / k6 | `3.2.26-alpine` (LTS line) / `3.1.6` / `2.3.0` |

| Port | Service | Exposure |
|---|---|---|
| 80, 443 | Caddy (management), HAProxy (load balancer) | public |
| 8443 | relief app (HTTPS) | public on application VMs |
| 9464 | relief `/metrics` | application VM, scraped by Prometheus |
| 9100 | node_exporter (TLS and basic auth) | application VM |
| 8404 | HAProxy statistics and Prometheus exporter (TLS, `stats auth`) | load balancer VM |
| 9999 | HAProxy Runtime API (mTLS, project-CA client certificate, allow-listed commands) | load balancer VM; the runner only |
| 8080 | ops API and dashboard (plain HTTP) | Compose network only; Caddy proxies it |
| 9465 | ops `/metrics` (`OPS_METRICS_PORT`) | Compose network only; no Caddy route, no host port |
| 9090, 9093, 27017, 5672, 6379 | Prometheus, Alertmanager, MongoDB, RabbitMQ, Redis | Compose network only, no host ports |
| 18443, 55433 | local T31 run only (`compose.local.yaml`), bound to 127.0.0.1 | developer machine, never deployed |

Ops `/metrics` serves `cdr_member_serving`, `cdr_supabase_db_size_bytes`, `cdr_supabase_keepalive_last_success_timestamp`, `cdr_backup_latest_capture_timestamp`, `cdr_policy_active_version` and `cdr_ops_metrics_query_success`. A series whose source query fails or has no row is omitted, never reported as 0.

The StackStorm `cdr` pack (`drproj-mgmt/automation/stackstorm/packs/cdr`) carries the 6-hour keep-alive (`core.st2.IntervalTimer`) and the `cdr_alert` webhook rule (`POST /v1/webhooks/cdr_alert`, `St2-Api-Key`), which runs `cdr.record_alert` to record Alertmanager alerts in `ops.processed_events`. Since Phase 4 the pack also runs the capacity, reconcile, failover and policy workflows; Terraform runs only through `cdr.run_heavy` (docs/07). `st2-register` mounts the pack config so `--register-all` registers it. Monitoring files live in `drproj-mgmt/monitoring/` (`rules/`, `targets/relief/` file_sd, `grafana/provisioning` and `grafana/dashboards`) and are mounted read-only into Prometheus and Grafana. The runner image (`automation/runner/Dockerfile`) bundles pinned Terraform with an offline provider mirror, ansible-core with its collections and the PostgreSQL client, and refuses to build without explicit Terraform, ansible-core and PostgreSQL client versions.

Recovery procedures (orphan adoption, HAProxy VM recovery, Supabase pause check, restore rollback) are in docs/15-runbooks.md.

Do not expose the ops API credential configuration through a frontend or bundle it into React. Registry credentials are read-only on application hosts. Keep native stores/queues on the internal management Compose network.

### Management stack defaults (MGMT-STACK)

- Secrets live in `/etc/drproj/mgmt/` (0700): `mgmt.env` (Compose environment, 0600) and `secrets/` (`MGMT_SECRETS_DIR`). Passwords are created by the role with `ansible.builtin.password` and kept; files read inside containers are owned by the container user (st2 uid 103/gid 104, Grafana 472, nobody 65534), mode 0600, except `st2_api_key` (0640: Alertmanager as owner, st2 as group). Docker bind mounts keep host ownership, so `root:root 0600` is unreadable to the st2 services.
- The operator password is generated (24 alphanumerics) because the API requires at least 12 characters; it is stored in `/etc/drproj/secrets/ops-operator.pw`, the Caddy hash (bcrypt, `htpasswd -B` from stdin) in `/etc/drproj/mgmt/secrets/ops_basic_auth_hash`.
- `ACME_CA` is derived from `ingress.acme_staging` (staging first, then production). Caddy needs `ingress.acme_email`.
- The pack's database login is a dedicated `stackstorm` role in `dr_ops` and `dr_automation` (`scripts/db-init/create-stackstorm-login.sh`).
- The ops API and the actionrunner trust the Supabase root CA through `NODE_EXTRA_CA_CERTS` / `PGSSLROOTCERT` (`compose.ansible.yaml`); verification is never disabled.
- Prometheus loads the real-deployment `node` job through `scrape_config_files` (mounted only by `compose.ansible.yaml`); file_sd targets for the primary are generated into `monitoring/targets/{relief,node}/primary.json` (gitignored). The node_exporter scrape password is read once from the primary and stored on management (0600, owner nobody).
- `provider_adapter: real` is configured without tokens (`token_env` names only, `REPLACE_ME_BEFORE_PROVISIONING` for region/size/image). The 60 s reconcile rule is disabled until tokens exist; the 6 h keep-alive rule is enabled.

