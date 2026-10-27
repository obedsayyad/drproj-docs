#!/usr/bin/env python3
"""Rebuild PROJECT_HANDOFF.md (concatenation of the bundle, in a fixed order) and SHA256SUMS. Run from the drproj-docs root:
python3 scripts/build-handoff.py. Check afterwards: shasum -a 256 -c SHA256SUMS."""
import glob
import hashlib
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HEADER = ("# Complete project documentation and conversation handoff\n\n"
          "Design baseline: 22 October 2026. Documentation only; no implementation/deployment has occurred.\n\n"
          "Read source headings for individual-file boundaries. The docs folder remains the canonical editable bundle.\n\n\n---\n\n\n---\n\n\n---\n\n")
ORDER = (["README.md", "WORK_PROJECT_START.md", "CLAUDE.md"] + sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs", "[0-9]*.md")))
         + ["config/deployment-inputs.example.json", "config/policy-defaults.json", "spec/events.schema.json", "spec/openapi.yaml"])


def main():
    parts = []
    for rel in ORDER:
        with open(os.path.join(ROOT, rel)) as f:
            parts.append(f"# Source: {rel}\n\n{f.read()}")
    with open(os.path.join(ROOT, "PROJECT_HANDOFF.md"), "w") as f:
        f.write(HEADER + "\n\n---\n\n".join(parts))
    listed = sorted(ORDER + ["PROJECT_HANDOFF.md"])
    with open(os.path.join(ROOT, "SHA256SUMS"), "w") as f:
        for rel in listed:
            with open(os.path.join(ROOT, rel), "rb") as g:
                f.write(f"{hashlib.sha256(g.read()).hexdigest()}  {rel}\n")


if __name__ == "__main__":
    main()
