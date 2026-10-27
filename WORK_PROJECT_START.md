# Start the next ChatGPT Work project

## Place these files

Working directory: the project root is collageDRproject/ and the documentation bundle is in collageDRproject/docs/. This is a plain folder, not a ZIP. Copy the entire collageDRproject directory (docs/ plus any sibling implementation repositories placed next to it) to:
/Users/obedsayyad/Documents/collageDRproject

Path convention: file paths in this document, README.md and CLAUDE.md are relative to the docs bundle root (collageDRproject/docs/); numbered documents are written as docs/NN-name.md within that bundle.

Create/open the next ChatGPT Work project with that directory and its documentation attached/available. This document does not itself create a ChatGPT project.

## Paste this starting prompt

Read README.md, CLAUDE.md, all docs, config/policy-defaults.json, and the API/event specifications in this folder. These documents capture the completed architecture discussion for my college disaster-recovery demonstration. docs/14-conversation-record.md preserves the detailed project decisions and corrections.

We are using Node.js, Supabase Free, Docker Compose on separate public-IP VMs, StackStorm, Terraform, Ansible, HAProxy, Prometheus/Grafana, and k6. The fixed primary/management/LB/testing VMs are self-hosted. There is one warm application standby, on DigitalOcean by default (or Linode). Elastic cloud application VMs are added one per step, alternating DigitalOcean and Linode. The cloud cap is up to 5 DigitalOcean and up to 5 Linode VMs, counting the standby and provisioning/cleanup-pending resources.

The relief application has no login; dashboard views are public, and mutating ops operations require the shared operator password (Caddy Basic Auth). Do not add Supabase Auth, custom RBAC, firewall setup, VPNs, AWS, Kubernetes, Pulumi, AWX, managed PITR, or Restic. HAProxy handles app traffic only. DNS is Vercel, with app.cproj.obed.one on the LB and management subdomains directly on the management host.

Use the documented editable defaults. Back up application data through daily pg_dump at 00:00 UTC and retain it 30 days (unencrypted, dir 0700 / files 0600 on the management VM). Restore only the app schema, preserving ops records and maintenance controls.

I want to build this with Claude Code. Help me implement it phase by phase from the accepted design. Use reasonable engineering defaults and ask only for genuinely missing deployment facts or meaningful unresolved requirements. Keep your chat responses short. Clearly distinguish documentation, implemented behavior, test adapters, real integrations, and executed validation. Start by inspecting this folder and checking that you understand the documented constraints.

## For Claude Code

CLAUDE.md is the direct implementation handoff. Start with the phase gates in docs/11-build-and-demo-plan.md. Actual deployment inputs remain unset in config/deployment-inputs.example.json. No app code or cloud resources were created during planning.

## First environment information to gather when deployment is requested

Existing VM public IPs and SSH access; cloud account credentials/available regions; Supabase project connections; GitHub repository/registry namespace; storage capacity; ACME contact. Do not request secrets pasted into public documentation or source.

## Current completion status

The architectural plan, decisions, policy defaults, contracts, acceptance criteria, and handoff context are documented. Runtime compatibility, software pins, resource headroom, actual infrastructure creation, DNS edits, certificates, app implementation, migrations and acceptance tests remain to be performed.
