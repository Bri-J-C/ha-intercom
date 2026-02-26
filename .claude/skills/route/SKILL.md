---
name: route
description: Route requests to the best-suited specialized agent based on intent. Use when the user says "/route" followed by a task description.
allowed-tools:
  - Task
---

Route the user's request to the best-suited specialized agent. Analyze the intent and dispatch accordingly.

## Routing Table

| Intent Keywords | Agent | Use For |
|----------------|-------|---------|
| research, documentation, api, version, spec, "what is", "how does" | researcher | Gathering authoritative info, version-specific details, API docs |
| implement, build, create, write code, develop | automation-architect | Production-ready code from specs/briefs |
| review, improve, clean up, code quality | pragmatic-code-reviewer | Reviewing code for correctness, maintainability, brief alignment |
| spec, compliance, version check, deprecated, api exists | spec-compliance-auditor | Verifying code against official docs, API specs, standards |
| validate, complete, final check, "is this done" | completion-validator | Final sanity check before delivery |
| debug, trace, root cause, "why is this broken", crash, error | ultrathink-debugger | Complex bugs, race conditions, mysterious failures |
| context, summarize, history, load context, save, persist, memory | context-persistence-manager | Preserving/restoring context across sessions |
| ux, user experience, accessibility, project management, sign-off | karen | PM reality checks, UX review, ship/no-ship verdicts |

## Instructions

1. Read the user's request (passed as $ARGUMENTS)
2. Match the intent against the routing table above
3. If the request clearly matches one agent, launch it via the Task tool with a detailed prompt
4. If the request spans multiple agents, launch them in parallel where possible
5. If no clear match, use automation-architect as the default
6. Always pass the full user request context to the agent

The user's request: $ARGUMENTS
