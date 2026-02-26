Update all README files to reflect the current state of the project.

Launch the documentation-engineer agent (via Task tool) to:

1. Read the current source files to understand the actual state:
   - `firmware/main/protocol.h` — firmware version, protocol constants
   - `intercom_hub/intercom_hub.py` — hub version, features
   - `intercom_hub/www/ptt-v7.js` — web PTT features
   - `firmware/main/main.c` — firmware features and architecture
   - `CLAUDE.md` — current feature list and architecture notes

2. Update these README files to match:
   - `README.md` — root project overview, feature list, version table
   - `firmware/README.md` — firmware features, build instructions, version
   - `intercom_hub/README.md` — hub features, configuration, version table

3. Ensure:
   - Version numbers match the source files exactly
   - Feature lists are current (no stale features, no missing new ones)
   - No references to removed/deprecated functionality
   - Mermaid diagrams use `<br/>` for line breaks (not `\n`)
   - No internal IPs in public docs (use placeholder names)

4. Report what changed in each file.

Do NOT commit — just update the files and report.

Working directory: /home/user/Projects/assistantlisteners
