#!/bin/bash
# schedule-resume.sh — Schedule a one-shot cron job to resume a Claude Code session
#                      after a rate limit reset.
#
# Usage:
#   tools/schedule-resume.sh <reset-time> [session-id]
#   tools/schedule-resume.sh --cancel
#
# reset-time formats:
#   "14:30"        24-hour time (today, or tomorrow if already past)
#   "2:30pm"       12-hour time with am/pm
#   "+5h"          relative: 5 hours from now  (also: +5hours, +90m, +90minutes)
#   "+30m"         relative: 30 minutes from now
#   1234567890     unix timestamp

set -euo pipefail

# ── Constants ──────────────────────────────────────────────────────────────────
CRON_MARKER="# claude-resume-job"
BUFFER_MINUTES=2      # pad added to the reset time before scheduling
WRAPPER_DIR="/tmp"

# ── Helpers ───────────────────────────────────────────────────────────────────
die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
    cat >&2 <<EOF
Usage:
  $(basename "$0") <reset-time> [session-id]
  $(basename "$0") --cancel

reset-time formats:
  "14:30"        24-hour HH:MM (today or tomorrow if already past)
  "2:30pm"       12-hour time with am/pm
  "+5h"          relative from now (also +5hours, +30m, +30minutes)
  1234567890     unix timestamp

Examples:
  $(basename "$0") 14:30
  $(basename "$0") "2:30pm" abc123session
  $(basename "$0") +5h
  $(basename "$0") --cancel
EOF
    exit 1
}

# Resolve the 'claude' binary. Prefer the PATH version; fall back to common
# locations. Die clearly if not found — a silent wrong path would be worse.
find_claude() {
    if command -v claude &>/dev/null; then
        command -v claude
        return
    fi
    for candidate in \
        "$HOME/.local/bin/claude" \
        "/usr/local/bin/claude" \
        "/opt/claude/bin/claude"
    do
        if [[ -x "$candidate" ]]; then
            echo "$candidate"
            return
        fi
    done
    die "claude binary not found in PATH or common locations. Install Claude Code first."
}

# Parse a relative offset like "+5h", "+90m", "+5hours", "+30minutes"
# Prints the equivalent 'date -d' argument string (e.g. "5 hours" or "90 minutes").
parse_relative() {
    local raw="${1#+}"          # strip leading +
    local num

    if [[ "$raw" =~ ^([0-9]+)(hours?|h)$ ]]; then
        num="${BASH_REMATCH[1]}"
        echo "${num} hours"
    elif [[ "$raw" =~ ^([0-9]+)(minutes?|m)$ ]]; then
        num="${BASH_REMATCH[1]}"
        echo "${num} minutes"
    else
        die "Unrecognised relative time format: '$1'. Use +Nh or +Nm (e.g. +5h, +30m)."
    fi
}

# Given a reset-time string, return a unix timestamp with BUFFER_MINUTES added.
# Relies on GNU date (standard on Linux).
parse_reset_time() {
    local raw="$1"
    local epoch

    if [[ "$raw" =~ ^\+ ]]; then
        # Relative offset
        local offset_str
        offset_str="$(parse_relative "$raw")"
        epoch=$(date -d "now + ${offset_str} + ${BUFFER_MINUTES} minutes" +%s) \
            || die "date could not parse relative offset '${raw}'"

    elif [[ "$raw" =~ ^[0-9]{10,}$ ]]; then
        # Unix timestamp (10+ digits). GNU date doesn't support arithmetic in
        # the '@epoch + N minutes' form, so add the buffer seconds directly.
        epoch=$(( raw + BUFFER_MINUTES * 60 ))

    else
        # Absolute time: validate format before handing to date -d to prevent
        # arbitrary string injection into the shell via date's error path.
        if [[ ! "$raw" =~ ^[0-9]{1,2}:[0-9]{2}([aApP][mM])?$ ]]; then
            die "Unrecognised time format: '${raw}'. Use: 14:30, 2:30pm, +5h, +30m, or a unix timestamp."
        fi

        epoch=$(date -d "${raw}" +%s 2>/dev/null) \
            || die "date could not parse time '${raw}'. Try formats: 14:30, 2:30pm, +5h, +30m"

        # If the parsed time is already in the past (more than 1 min ago), schedule
        # for the same time tomorrow. This handles "14:30" when it's 14:35.
        local now
        now=$(date +%s)
        if (( epoch < now - 60 )); then
            epoch=$(date -d "tomorrow ${raw}" +%s 2>/dev/null) \
                || die "Could not compute tomorrow's date for '${raw}'"
            echo "Note: '${raw}' is already past — scheduling for tomorrow." >&2
        fi

        # Add the buffer AFTER resolving today-vs-tomorrow
        epoch=$(( epoch + BUFFER_MINUTES * 60 ))
    fi

    echo "$epoch"
}

# Convert a unix timestamp to cron fields "MIN HOUR DOM MON DOW"
epoch_to_cron() {
    local epoch="$1"
    date -d "@${epoch}" +'%M %H %d %m *'
}

# ── Cancel mode ───────────────────────────────────────────────────────────────
cancel_jobs() {
    # Find pending resume jobs by searching for our marker in crontab
    local current_cron
    if ! current_cron=$(crontab -l 2>/dev/null); then
        echo "No crontab found — nothing to cancel."
        return
    fi

    # Count matching lines. grep returns exit 1 when no match — suppress that
    # with '|| true' so set -e doesn't abort us.
    local job_lines
    job_lines=$(echo "$current_cron" | grep -c "claude-resume-job" || true)

    if (( job_lines == 0 )); then
        echo "No pending claude-resume jobs found in crontab."
        return
    fi

    # Remove all claude-resume blocks (the marker comment line + the cron line
    # immediately following it). Using awk pattern: on seeing the marker, set a
    # skip counter of 2 so both the comment and the next line are dropped.
    local new_cron
    new_cron=$(echo "$current_cron" | awk '
        /claude-resume-job/ { skip=2 }
        skip > 0 { skip--; next }
        { print }
    ')

    echo "$new_cron" | crontab -
    echo "Removed ${job_lines} pending claude-resume job(s) from crontab."

    # Also clean up any leftover wrapper scripts in /tmp
    rm -f "${WRAPPER_DIR}"/claude-resume-*.sh
    echo "Cleaned up any /tmp/claude-resume-*.sh wrapper scripts."
}

# ── Main ──────────────────────────────────────────────────────────────────────
[[ $# -lt 1 ]] && usage

if [[ "$1" == "--cancel" ]]; then
    cancel_jobs
    exit 0
fi

RESET_TIME_ARG="$1"
SESSION_ID="${2:-}"

if [[ -n "$SESSION_ID" ]] && [[ ! "$SESSION_ID" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    die "Invalid session ID '${SESSION_ID}' — must be alphanumeric/hyphen/underscore only."
fi

# Validate cron is available before doing any work
if ! command -v crontab &>/dev/null; then
    die "crontab not found. This script requires cron to be installed."
fi

CLAUDE_BIN="$(find_claude)"
PROJECT_DIR="$(pwd)"

# Parse the time and produce cron fields
EPOCH="$(parse_reset_time "$RESET_TIME_ARG")"
CRON_FIELDS="$(epoch_to_cron "$EPOCH")"
HUMAN_TIME="$(date -d "@${EPOCH}" '+%A %Y-%m-%d at %H:%M')"

# Build the wrapper script path — unique per invocation so multiple jobs can coexist
WRAPPER_SCRIPT="${WRAPPER_DIR}/claude-resume-$$-$(date +%s).sh"

# Build the claude command.
# --print (-p) runs non-interactively: processes the prompt and exits when done.
# This is essential for headless cron execution — without it, Claude waits for
# user input after its first response.
RESUME_PROMPT="Resuming after rate limit. Read .claude/project-log.md, restore full context, and continue executing the plan autonomously. Do not ask what to work on — just pick up where the last session left off and execute."

if [[ -n "$SESSION_ID" ]]; then
    CLAUDE_CMD="${CLAUDE_BIN} --dangerously-skip-permissions --print --resume ${SESSION_ID} \"${RESUME_PROMPT}\""
    SESSION_DISPLAY="session ${SESSION_ID}"
else
    CLAUDE_CMD="${CLAUDE_BIN} --dangerously-skip-permissions --print --continue \"${RESUME_PROMPT}\""
    SESSION_DISPLAY="most recent session (--continue)"
fi

# Write the wrapper script. It:
#   1. cd to the project directory
#   2. Runs claude
#   3. Removes itself from crontab by filtering out the entry that references it
#   4. Deletes itself
cat > "$WRAPPER_SCRIPT" << WRAPPER
#!/bin/bash
# One-shot Claude Code session resume — auto-generated by schedule-resume.sh
# This script removes itself from crontab after running.

LOG="/tmp/claude-resume.log"
exec >> "\$LOG" 2>&1
echo "=== Claude resume fired at \$(date) ==="

cd "${PROJECT_DIR}" || exit 1

${CLAUDE_CMD}
EXIT_CODE=\$?

# Self-removal: filter our wrapper path out of crontab (fixed-string match avoids
# regex metacharacter issues in the path)
( crontab -l 2>/dev/null | grep -Fv "${WRAPPER_SCRIPT}" ) | crontab -

# Clean up this wrapper script
rm -f "${WRAPPER_SCRIPT}"

exit \$EXIT_CODE
WRAPPER

chmod +x "$WRAPPER_SCRIPT"

# Install the cron job. Append to existing crontab (or create new one).
(
    crontab -l 2>/dev/null || true
    echo "${CRON_MARKER}"
    echo "${CRON_FIELDS} ${WRAPPER_SCRIPT}"
) | crontab -

# ── Confirmation output ───────────────────────────────────────────────────────
echo ""
echo "Claude Code resume scheduled."
echo ""
echo "  Time:     ${HUMAN_TIME} (${BUFFER_MINUTES} min buffer added)"
echo "  Session:  ${SESSION_DISPLAY}"
echo "  Project:  ${PROJECT_DIR}"
echo "  Command:  ${CLAUDE_CMD}"
echo "  Wrapper:  ${WRAPPER_SCRIPT}"
echo ""
echo "You can safely close this terminal. Claude will start automatically."
echo ""
echo "To cancel:"
echo "  $(basename "$0") --cancel"
echo "  or: crontab -e   (remove the '${CRON_MARKER}' block)"
echo ""
