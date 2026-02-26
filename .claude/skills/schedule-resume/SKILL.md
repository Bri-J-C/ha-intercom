---
name: schedule-resume
description: Schedule automatic session resume after a rate limit reset. Use when hitting a Claude Code rate limit and wanting to continue automatically after the reset window expires. Saves session state, schedules the cron job, and tells the user they can close the terminal.
argument-hint: <reset-time>
user-invocable: true
allowed-tools: Read, Bash
model: haiku
context: inline
version: 1.0.0
---

# Schedule Session Resume After Rate Limit

This skill schedules a one-shot cron job to automatically resume the current
Claude Code session at the rate limit reset time.

## When to Use
- User reports hitting a rate limit and sees a reset time
- User says "schedule resume for [time]" or "/schedule-resume [time]"
- PM invokes this after saving session state at end of a rate-limited session

## Steps

### Step 1 — Confirm the reset time
The user will have told you the reset time from the rate limit message. If not
provided, ask: "What time does the rate limit reset?" Accept any of these formats:
- `14:30` — 24-hour clock (today or tomorrow if past)
- `2:30pm` — 12-hour clock
- `+5h` — relative hours from now
- `+30m` — relative minutes from now
- Unix timestamp

### Step 2 — Get the current session ID
Run the following to find the most recent session transcript file, which encodes
the session ID in its filename:

```bash
ls -t ~/.claude/projects/-home-user-Projects-assistantlisteners/*.jsonl 2>/dev/null | head -1
```

The filename format is `<session-id>.jsonl`. Extract just the basename without
the `.jsonl` extension — that is the session ID.

If the directory is empty or the command fails, proceed without a session ID
(Claude will auto-resume the most recent session).

### Step 3 — Schedule the resume
Run:

```bash
cd /home/user/Projects/assistantlisteners
tools/schedule-resume.sh <reset-time> [session-id]
```

Where `<reset-time>` is what the user provided and `[session-id]` is from Step 2
(omit if not found).

Report the full output of the script to the user verbatim.

### Step 4 — Confirm to user
Tell the user:
- The resume is scheduled for [time] with [N] minutes of buffer
- They can safely close the terminal now
- Claude will automatically start and resume where things left off
- To cancel: run `tools/schedule-resume.sh --cancel`
- If on a laptop, keep the machine awake — cron won't fire during sleep

## Important Notes
- ALWAYS ensure the record-keeper has saved session state BEFORE running this
  skill. If the PM has not already called record-keeper, do NOT proceed — tell
  the user that session state must be saved first, and ask them to wait while
  the PM calls record-keeper.
- This skill does not save session state itself — that is the PM's job via
  record-keeper before invoking this skill.
- The 2-minute buffer is built into the script. Do not manually add extra time
  to the reset time the user gives you.
