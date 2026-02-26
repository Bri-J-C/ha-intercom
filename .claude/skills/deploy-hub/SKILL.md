---
name: deploy-hub
description: Deploy the intercom hub add-on to Home Assistant. Use when deploying hub changes to the HA server at 10.0.0.8. Covers file copy, rebuild, and log verification.
allowed-tools: Bash, Read
version: 1.0.0
---

# Deploy Hub Add-on

## Pre-flight
- Confirm `VERSION` in `intercom_hub.py` has been updated
- Confirm code has passed code-review and tests before deploying

## Step 1 — Copy Files
```bash
scp intercom_hub/intercom_hub.py root@10.0.0.8:/addons/intercom_hub/
scp intercom_hub/www/* root@10.0.0.8:/addons/intercom_hub/www/
```

## Step 2 — Rebuild (ALWAYS required after any change)
```bash
ssh root@10.0.0.8 "ha apps rebuild local_intercom_hub"
```
Wait for rebuild to complete before proceeding.

## Step 3 — Verify
```bash
ssh root@10.0.0.8 "ha apps logs local_intercom_hub --lines 30"
```
Check for: startup messages, no ERROR lines, MQTT connection confirmed, WebSocket server listening.

## Step 4 — Confirm
Report to PM: version deployed, any warnings in logs, and whether a firmware sync is needed for protocol changes.

## Rollback
Previous version is not automatically preserved. If rollback is needed, restore from git and redeploy.
