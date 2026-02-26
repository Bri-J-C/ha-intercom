# Rate Limit — Session Resume Protocol

## When a Rate Limit Is Hit

When Claude Code hits a rate limit and the session is about to end, the PM
must do the following **in order** before the session terminates:

### Step 1 — Save session state (mandatory)
Call `record-keeper` with a full session summary:
- What was in progress at the moment of the rate limit
- Any decisions made, files changed, tasks completed this session
- Current blockers and next actions for the resumed session
- Anything the next session needs to know to pick up seamlessly

Do not skip this step. A resumed session with no state saved is nearly
useless — the agent will have to re-read everything from scratch.

### Step 2 — Offer auto-resume
Tell the user:

> "Rate limit hit. Session state has been saved to project-log.md. If you
> tell me your reset time, I can schedule Claude to resume automatically
> — you can then close the terminal and walk away."

### Step 3 — Schedule the resume (if user wants it)
If the user provides a reset time, invoke the `schedule-resume` skill:

```
/schedule-resume <reset-time>
```

The skill will:
- Find the current session ID
- Schedule a one-shot cron job with a 2-minute buffer
- Print confirmation with the exact scheduled time and cancel instructions

### Step 4 — Confirm safe to close
After the cron job is installed, tell the user:

> "Resume is scheduled. You can safely close this terminal now."
- If on a laptop, keep the machine awake — cron won't fire if the machine is asleep

## Cancel a Scheduled Resume
```bash
tools/schedule-resume.sh --cancel
```

## Accepted Reset Time Formats
| Format | Example | Meaning |
|---|---|---|
| 24-hour | `14:30` | Today at 14:30 (tomorrow if past) |
| 12-hour | `2:30pm` | Today at 2:30 PM (tomorrow if past) |
| Relative hours | `+5h` | 5 hours from now |
| Relative minutes | `+30m` | 30 minutes from now |
| Unix timestamp | `1234567890` | Exact epoch second |
