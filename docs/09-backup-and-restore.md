# pg_dump backup and staged recovery

## Scope

Supabase Free is selected. Use pg_dump/pg_restore; do not assume managed downloadable daily backups or PITR are available. No Restic or separate backup platform is needed.

Backup only application schema definitions and records, including request updates and idempotency data. Uploaded files, Supabase Auth, and full Supabase project recovery are outside the selected scope. Ops records, maintenance controls, StackStorm databases, and Terraform state are not included in an app-schema dump.

## Schedule/storage

Daily at 00:00 UTC (05:30 IST), retaining 30 days. Both settings are editable. Management persistent storage contains the backup files and manifests; it is outside the application VMs but not outside the management host's failure domain.

Pin pg_dump, pg_restore and psql, and the restore-test container's Postgres image, to the SAME major version as the Supabase server (`postgres_server_major` in deployment inputs). Verification proves the exact tool and image combination. Use the session pooler (port 5432) connection for dumps; do not expose connection secrets in process output or logs. Keep backup credentials inside the execution environment.

Backups are plain `pg_dump -Fc` custom-format files, stored only on the management VM in a directory with mode 0700 and files with mode 0600. Verification runs automatically after capture and needs no keys.

## Backup job

1. Acquire the backup job lease; prevent overlapping dumps.
2. Capture source database identifier, app schema revision, capture start time, and operation ID. Insert the ops.backups row at the start with capture_status `capturing`, verification_status `unverified`.
3. Run a custom-format pg_dump scoped to app, writing a temporary file: `pg_dump -Fc -n app ... -f file.dump`. Create the directory 0700 and the file 0600.
4. On successful exit, record finish time, byte size (`bytes`), SHA-256, and manifest, and set capture_status `captured`. On failure set `failed` with error_code and error_message.
5. Atomically move to the completed artifact directory and update the ops catalogue row.
6. Run verification after capture (policy `backup.verify`: enabled, after_capture, timeout_seconds 900). Report a successful capture separately from a successful restore verification.
7. Expire eligible completed artifacts older than 30 days after successful backup creation: retention is computed from capture finish plus the policy days, and expiry sets `expired_at`.
8. Preserve safety backups referenced by active recovery operations; never delete the file currently being written.

On failure retry once after 60 seconds; then raise a dashboard incident. Freshness uses the last successful capture, not the scheduled start time. If backup jobs are missed, potential data loss exceeds a day; display actual backup age.

Prefer a regular automated isolated verification using the existing StackStorm schedule, initially after each daily capture if the management host has sufficient headroom. Serialize it with expensive infrastructure/restore work as needed. This verifies usability without introducing another backup platform.

## Restore verification

Operator selects a catalogue ID, not an arbitrary filesystem path. Verify checksum, manifest, source identity, format, schema revision and supported target compatibility.

Start an isolated PostgreSQL container/target with appropriate dependencies/roles. Restore the application schema without importing unrelated source ownership/privileges. Check expected tables, FK integrity, record counts, representative deterministic seeded records, idempotency behavior and application read queries. A checksum proves file integrity, not recoverability.

Destroy or reset isolated targets deliberately. A failed isolated verification must not mutate the live database. Verification sets verification_status (`verifying`, then `verified` or `failed`) and `verified_at`. Run it as a heavy job, not concurrently with capacity operations. Verification is started automatically after capture or by `POST /api/backups/{id}/verify` (202 with operation_id).

## Live recovery

Preconditions: `POST /api/restores` returns 422 unless the selected backup is `verified` and was verified no more than 7 days ago (policy `recovery.require_verified_backup` true, `max_verification_age_days` 7). Restore preflight: take the restored size of the selected backup from its isolated verification (`pg_database_size` or schema size in the restore-test container) and require `current_db_size + restored_app_size * 1.2 < 450 MB` (Supabase Free limit is 500 MB), because the old and new schemas coexist during the swap. The tools and the restore-test image must be pinned to the same major as the server.

1. Keep the selected dump immutable and record the recovery operation.
2. Set maintenance in ops.runtime_flags, outside the restored schema.
3. Every application instance acknowledges write suspension; wait for in-flight writes/background writes to drain.
4. Block writes in the application itself as well as gateway behavior, because all app VMs have public IPs and firewall automation is excluded.
5. Pause conflicting capacity/policy migrations and app-changing jobs.
6. Create a fresh safety dump of current app data before live mutation.
7. Restore by schema swap in one transaction, using the restore role:
   ```
   pg_restore -n app --no-owner --no-privileges --exit-on-error -f app.sql backup.dump
   psql --single-transaction -v ON_ERROR_STOP=1 -f pre.sql -f app.sql -f post.sql
   ```
   pre.sql:
   ```
   SET lock_timeout = '5s';
   ALTER SCHEMA app RENAME TO app_prev;
   ```
   post.sql re-grants the app runtime role and backup role on schema app and its tables, restores default privileges, and purges expired idempotency rows. idempotency_records is restored with the snapshot. Any error aborts the transaction and leaves app untouched. Hash ops.runtime_flags before and after; fail if the hash changed.
   Because the whole schema is swapped, objects that exist only in the current `app` (tables, columns, indexes or functions added after the backup was taken) move into `app_prev` with it and are absent from the restored `app`; they are dropped with `app_prev` after verification. Migrations newer than the backup's schema revision must be re-applied after the restore, or the application image digest must be pinned to the one matching that schema revision, before reopening traffic.
8. Verify the restored state through the actual relief API.
9. Only then drop app_prev, clear maintenance and reopen normal traffic.
10. Record restore start/finish, recovered capture point, validation evidence, and result.

App schema restore must not overwrite ops or maintenance state. Do not use whole-project Supabase restore or whole-database clean commands.

## Failure/rollback

Keep maintenance active on live-restore or validation failure. If the swap transaction fails, app is unchanged. If API verification fails after the swap, roll back by renaming back (`ALTER SCHEMA app RENAME TO app_failed; ALTER SCHEMA app_prev RENAME TO app;` in one transaction), then drop app_failed. If rename-back is not possible, attempt one restore from the fresh pre-restore safety dump if the connection/target is usable. Keep app_prev until API verification passes. Verify rollback before reopening traffic. If rollback cannot be verified, stop and raise an incident with the exact known state. Never report recovery complete just because pg_restore exited without checking the app.

Backups with a newer schema than supported by the current app are rejected until an explicitly compatible application revision/restore plan is selected.

## Demonstration and measurement

Create an immediate successful backup before the deleted-data demo. Record a seeded set of request IDs and expected values. Delete those demo records, initiate recovery, and compare the restored data.

Daily backups give an approximately 24-hour target data-loss window only when jobs succeed and complete as expected. Actual RPO is based on capture point, not file finish time. Measure actual RTO including maintenance and verification.

The same Supabase database is shared by all app VMs. Routing to another compute provider does not bypass restore maintenance or a Supabase outage.

## Supabase Free notes

- Projects pause after 7 days of low activity. A keep-alive `SELECT 1` runs every 6 hours; check the project is awake at T-24h and T-1h before the demo.
- The database limit is 500 MB; alert above 400 MB and refuse live restore unless `current_db_size + restored_app_size * 1.2 < 450 MB`.
- Use the session pooler (5432, IPv4); avoid named prepared statements.
- pg_dump/pg_restore/psql and the restore-test image must be the same major version as the server.
