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
- Keep a plain backup of the HAProxy config, the certificate bundle, and the acme account on the management VM (directory 0700, files 0600).

Recover:
1. Choose a replacement host (new or existing VM).
2. Restore config, certificate bundle, and acme account from the backup.
3. Run the Ansible `lb` role against the new host.
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
