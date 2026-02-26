---
name: full-pipeline
description: Complete build → review → test → deploy workflow for the intercom project. Use when a feature or fix is ready to go through the full quality pipeline from code to production. Orchestrates all agents in the correct sequence.
version: 1.0.0
---

# Full Pipeline Workflow

This skill defines the complete sequence the PM follows to take a code change from written to deployed safely.

## When to Use
- A feature has been implemented by code-writer and is ready for the full pipeline
- A bug fix is complete and needs to go through quality gates before deployment
- Explicitly requested by user ("run the full pipeline on this")

## Pipeline Sequence

### Stage 1 — Code Review
Delegate to `code-reviewer`:
- Review all changed files
- Brief with: what changed, what modules are affected, any protocol changes to check
- **Gate**: must return `APPROVED` or `APPROVED WITH SUGGESTIONS` before proceeding
- If `CHANGES REQUIRED`: return to code-writer with reviewer feedback

### Stage 2 — Testing
Delegate to `tester`:
- Run all existing tests
- Build targeted tests for the changed functionality
- Brief with: what was changed, reviewer's "Notes for Tester" section, any edge cases specific to this change
- **Gate**: must return `ALL PASSED` before proceeding
- If `FAILURES DETECTED`: determine if failure is in changed code (→ code-writer) or pre-existing (→ flag to PM for decision)

### Stage 3 — Version Update (if not already done)
Verify before deploying:
- Hub change? → `VERSION` updated in `intercom_hub.py`?
- Firmware change? → `FIRMWARE_VERSION` updated in `firmware/main/protocol.h`?
- Protocol change? → Both updated and in sync?

If not updated, delegate to `code-writer` to update version strings before proceeding.

### Stage 4 — Deployment
Delegate to `devops`:
- Hub changes: invoke `deploy-hub` skill
- Firmware changes to directly-connected device: invoke `flash-firmware` skill
- Firmware changes to INTERCOM2: invoke `flash-intercom2` skill
- Both changed: deploy hub first, then firmware

### Stage 5 — Post-Deploy Verification
After devops reports success:
- Confirm hub logs show clean startup (no errors)
- Confirm firmware version string matches expected
- For audio changes: manual audio test recommended — flag this to user

### Stage 6 — Record
Call `record-keeper` with:
- What was deployed, versions, which devices
- Any issues encountered during pipeline
- Current project state

## Abort Conditions
Stop the pipeline and report to user if:
- Code review returns `CHANGES REQUIRED` after two iterations
- Tests fail and root cause is unclear
- Devops deployment fails
- Any agent returns unexpected output that doesn't fit normal patterns
