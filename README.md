# collageDRproject — architecture and Claude Code handoff

Cloud-Based Disaster Relief Resource Allocation and Coordination System  
Design baseline: 22 October 2026. Status: architecture documentation; implementation has not started.

## Purpose

Demonstrate cloud compute allocation and digital disaster recovery using a small relief-request application. The system routes traffic to healthy servers, activates existing standby capacity, provisions replacement/overflow VMs, coordinates incidents, and backs up/restores application data.

This is a college demonstration. Infrastructure resources mean compute capacity. Food, water, medicine, and shelter are request categories in the sample app; physical inventory optimization is outside the baseline.

## Project layout

collageDRproject/ is the project root (working directory for the project). This documentation bundle lives in collageDRproject/docs/ and is one part of the project. Path convention: every file path in this README, CLAUDE.md and WORK_PROJECT_START.md is relative to the bundle root collageDRproject/docs/ (so docs/01-requirements-and-decisions.md is collageDRproject/docs/docs/01-requirements-and-decisions.md on disk, and config/ and spec/ sit beside it). Sibling repositories are referred to by name, relative to collageDRproject/. The bundle is the documentation and contract set. Implementation components (relief app, operations dashboard/API, infra with Terraform/Ansible, StackStorm pack) may live in separate repositories as sibling folders alongside it, each with its own build and test commands. Do not assume a single monorepo.

## Start here

1. Read CLAUDE.md for implementation constraints and build order.
2. Read docs/01-requirements-and-decisions.md for confirmed scope.
3. Read docs/02-architecture.md and docs/04-workflows.md.
4. Use config/policy-defaults.json as the initial policy specification.
5. Follow docs/11-build-and-demo-plan.md and its acceptance gates.

## Portable handoff files

- WORK_PROJECT_START.md: prompt and context for a new ChatGPT Work project.
- CLAUDE.md: implementation instructions for Claude Code.
- PROJECT_HANDOFF.md: combined copy of all documentation and contracts.
- docs/14-conversation-record.md: detailed chronological project conversation digest.
- docs/15-runbooks.md: operator runbooks for manual recovery.

The user explicitly requested local workspace output. The collageDRproject folder on the Mac now holds this bundle as `drproj-docs/` next to the `drproj-app/` and `drproj-mgmt/` repositories (docs/11, GitHub repositories).

## Documentation index

| Document | Contents |
|---|---|
| docs/01-requirements-and-decisions.md | Confirmed decisions and resolved alternatives |
| docs/02-architecture.md | Topology, services, traffic paths, ownership |
| docs/03-policies.md | Editable defaults and activation rules |
| docs/04-workflows.md | Scaling, failover, failback, failures, reconciliation |
| docs/05-data-model.md | Schemas, tables, invariants, idempotency |
| docs/06-api-and-events.md | Public APIs, internal events, command contracts |
| docs/07-automation.md | StackStorm, Terraform, Ansible, locking and state |
| docs/08-containers-networking-tls.md | Compose roles, ports, DNS, TLS, persistent storage |
| docs/09-backup-and-restore.md | Daily pg_dump, retention, staged recovery |
| docs/10-monitoring-and-tests.md | Metrics, k6 scenarios, acceptance matrix |
| docs/11-build-and-demo-plan.md | Repository layout, build dependencies, demo script |
| docs/12-boundaries-and-deployment-inputs.md | Accepted limitations and actual deployment inputs |
| docs/13-reference-sources.md | Official technical references |
| docs/14-conversation-record.md | Chronological conversation digest and decisions |
| docs/15-runbooks.md | Operator runbooks: HAProxy VM recovery, orphan adoption, restore |
| config/policy-defaults.json | Initial editable policy values |
| config/deployment-inputs.example.json | Non-secret deployment input template |
| spec/openapi.yaml | API design contract |
| spec/events.schema.json | Internal alert envelope contract |

## Intended location on the developer's Mac

/Users/obedsayyad/Documents/collageDRproject

This bundle was prepared in a remote workspace that cannot access that Mac path. This is a plain folder, not a ZIP: copy the whole collageDRproject directory to /Users/obedsayyad/Documents, so the docs bundle is at /Users/obedsayyad/Documents/collageDRproject/docs and sibling repositories can sit next to it. Do not create a similarly named /Users directory on a Linux host and claim it is the developer's Mac.

## Claude Code starting prompt

Open the project root collageDRproject/ (the folder that contains docs/) in Claude Code and use:

"Read README.md, CLAUDE.md, and the architecture documents. Build this project incrementally according to the accepted design and policy defaults. Start with the application, database migrations, policy model, and local container setup. Keep real infrastructure integrations distinguishable from test adapters. Use reasonable defaults for routine details and ask only for genuinely missing deployment inputs. Do not add login systems, AWS, Kubernetes, VPN setup, or firewall automation. Validate each build phase before continuing. Do not deploy cloud resources until deployment is part of my request."

## Not implemented

This bundle itself contains no application sources (they are in `drproj-app` and `drproj-mgmt`), working Terraform modules, Ansible playbooks, StackStorm packs, live DNS records, credentials, or deployed containers in this bundle. The JSON and OpenAPI files are design specifications, not operational configuration. Planning decisions do not constitute evidence that deployment or tests passed.
