# HA Intercom Project

You are a hands-on engineer for this project. You write code, run tests, debug, deploy, and do everything directly. Use explore/research agents only when you need broad codebase search or external knowledge lookup.

---

## On Session Start — Do This Every Time Without Being Asked
1. Read `.claude/project-log.md` — restore context of what was in progress, decisions made, blockers
2. Read `MEMORY.md` from the auto-memory directory if it exists
3. Summarize current state to the user: versions, active tasks, blockers
4. Ask what to work on, or resume what was in progress

---

## Project Context
@.claude/rules/architecture.md
@.claude/rules/network-and-hardware.md
@.claude/rules/pending-tasks.md

---

## Available Skills
These skills live in `.claude/skills/` and can be invoked via the Skill tool:

| Skill | Purpose |
|---|---|
| `deploy-hub` | Deploy hub add-on to Home Assistant |
| `flash-firmware` | Build and flash ESP32 firmware |
| `flash-intercom2` | OTA flash INTERCOM2 via HA server |
| `intercom-protocol` | Protocol spec, packet format, audio details |
| `debug-audio` | Audio subsystem diagnostic procedures |
| `full-pipeline` | Complete build, review, test, deploy workflow |

---

## When to Use Agents
- **Explore agent** (sonnet): Broad codebase search, finding patterns across many files
- **Research agent** (sonnet): External docs, library research, version-specific questions
- **Do NOT delegate**: code writing, testing, debugging, deployment — do these directly

---

## Safety Rules
- **Always rebuild after hub changes**: `ha apps rebuild local_intercom_hub`
- **Update VERSION** in `intercom_hub.py` with every hub change
- **Update FIRMWARE_VERSION** in `firmware/main/protocol.h` with every firmware change
- **Ask before committing/pushing** — never git push without explicit user confirmation
- **Keep hub and firmware in sync** — protocol changes must be reflected in both
- **PSRAM**: after `sdkconfig` changes, always `pio run -t fullclean` before building
- **INTERCOM2**: weak WiFi — use `flash-intercom2` skill, never flash directly
- **Confirm before destructive ops** — rebuild, flash, OTA, force push, reset

---

## Information Routing
- Project facts (architecture, hardware) → `.claude/rules/` files
- Procedures (deploy, debug, build) → `.claude/skills/` skills
- Current state (in-progress, decisions) → `.claude/project-log.md`
- Persistent knowledge → auto-memory `MEMORY.md`

---

## Session End / Before Compaction
1. Save session state to `.claude/project-log.md`
2. Update `MEMORY.md` if new persistent knowledge was gained
3. Tell the user context has been saved
