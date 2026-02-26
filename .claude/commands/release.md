Automate the full release pipeline: update docs, stage, commit, merge to main, tag, push, create GitHub release, and update MEMORY.md.

Steps:

1. **Update documentation** — Launch the documentation-engineer agent (via Task tool) to update all READMEs (root, firmware/, intercom_hub/) to reflect the current state of the codebase. Review the agent's changes before proceeding.

2. **Determine versions** — Read `FIRMWARE_VERSION` from `firmware/main/protocol.h` and `VERSION` from `intercom_hub/intercom_hub.py`. The tag format is `vFIRMWARE_VERSION` (e.g. `v2.8.0`).

3. **Stage and commit** — Stage any doc changes from step 1. If there are already uncommitted code changes, stage those too. Commit with a descriptive message including version numbers.

4. **Push feature branch**:
   ```
   git push origin feature/display-room-selector
   ```

5. **Merge to main**:
   ```
   git checkout main
   git merge feature/display-room-selector
   git push origin main
   git checkout feature/display-room-selector
   ```

6. **Tag the release**:
   ```
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```

7. **Create GitHub release**:
   ```
   gh release create vX.Y.Z --title "vX.Y.Z" --notes "$(cat <<'EOF'
   ## Changes
   [Summarize key changes from recent commits since last tag]

   ## Versions
   - Firmware: vX.Y.Z
   - Hub: vA.B.C
   - Lovelace Card: vD.E.F
   EOF
   )"
   ```

8. **Update MEMORY.md** — Launch the memory-keeper agent (via Task tool) to update MEMORY.md with the new commit hash, tag, and mark the release as complete.

IMPORTANT:
- Ask the user before pushing or merging
- Never force-push
- Always verify the merge succeeded before tagging

Working directory: /home/user/Projects/assistantlisteners
