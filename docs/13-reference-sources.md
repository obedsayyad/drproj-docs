# Official technical references

Consulted during planning. Documentation and provider availability can change; verify compatible pinned versions during implementation. No cost estimates or current performance guarantees are encoded by these links.

| Topic | Source |
|---|---|
| Supabase backups, Free-plan export guidance, project restore scope | https://supabase.com/docs/guides/platform/backups |
| Supabase CLI backup/restore | https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore |
| PostgreSQL pg_dump | https://www.postgresql.org/docs/current/app-pgdump.html |
| PostgreSQL backup concepts | https://www.postgresql.org/docs/current/backup.html |
| Supabase connection modes | https://supabase.com/docs/guides/database/connecting-to-postgres |
| DigitalOcean Terraform provider | https://registry.terraform.io/providers/digitalocean/digitalocean/latest/docs |
| Linode Terraform provider | https://registry.terraform.io/providers/linode/linode/latest/docs |
| DigitalOcean Droplet provisioning | https://docs.digitalocean.com/products/droplets/how-to/create/ |
| Linode VM creation API | https://techdocs.akamai.com/linode-api/reference/post-linode-instance |
| StackStorm architecture | https://docs.stackstorm.com/overview.html |
| StackStorm Orquesta | https://docs.stackstorm.com/orquesta/index.html |
| StackStorm workflow model | https://docs.stackstorm.com/orquesta/languages/orquesta.html |
| StackStorm concurrency policy prerequisites | https://docs.stackstorm.com/reference/policies.html |
| StackStorm Web UI | https://docs.stackstorm.com/install/config/webui.html |
| StackStorm single-host deployment | https://docs.stackstorm.com/install/overview.html |
| StackStorm requirements | https://docs.stackstorm.com/install/system_requirements.html |
| Ansible architecture | https://docs.ansible.com/projects/ansible/latest/dev_guide/overview_architecture.html |
| Ansible encrypted configuration | https://docs.ansible.com/projects/ansible/latest/vault_guide/index.html |
| Docker Compose | https://docs.docker.com/compose/ |
| HAProxy statistics | https://www.haproxy.com/blog/exploring-the-haproxy-stats-page |
| Prometheus alert rules | https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/ |
| Prometheus reload API | https://prometheus.io/docs/prometheus/latest/management_api/ |
| Alertmanager configuration | https://prometheus.io/docs/alerting/latest/configuration/ |
| k6 request-rate tests | https://grafana.com/docs/k6/latest/testing-guides/api-load-testing/ |
| k6 constant arrival rate | https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/ |
| GitHub Actions Docker publishing | https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images |
| GHCR | https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry |
| Vercel DNS/external services | https://vercel.com/docs/domains/managing-dns-records |
| Caddy automatic HTTPS | https://caddyserver.com/docs/automatic-https |
| acme.sh | https://github.com/acmesh-official/acme.sh |
| acme.sh HAProxy deployment hook | https://github.com/acmesh-official/acme.sh/blob/master/deploy/haproxy.sh |

## Alternatives evaluated but not selected

AWS, Kubernetes, Pulumi, AWX/Event-Driven Ansible, Semaphore UI, managed Cloudflare Load Balancing, Traefik as ingress, WireGuard, managed Supabase PITR, Restic. Their appearance in the conversation does not override the selected baseline. Historical recommendations were refined as the user clarified the college-demo scope and preferred tools.
