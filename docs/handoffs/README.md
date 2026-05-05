# Handoffs

Short, mid-task "where I left off" notes for in-flight work that is not yet doc-worthy. Lives in the repo so a fresh workstation (or a fresh chat session) can pick up cold without replaying conversation history.

## When to use this

| Situation | Use this | Use a design doc | Use auto-memory |
|---|---|---|---|
| Planned a workstream and need to align on shape | | ✅ `docs/design/<workstream>.md` | |
| Stopped mid-implementation; want to resume on another workstation tomorrow | ✅ | | |
| Half-debugged a deploy failure; need to remember what's been ruled out | ✅ | | |
| User preference / working-style note worth carrying across all future chats | | | ✅ |
| Permanent architecture decision | | ✅ `docs/design/` or `docs/architecture/` | |

If the note would still be useful in three months, it probably belongs in a design doc or `platforms/<platform>/CLAUDE.md` instead.

## Naming

One file per active workstream: `<workstream>.md` — e.g. `credential-rotation.md`, `walmart-onboarding.md`. Keep the name stable for the life of the workstream so cross-references don't rot.

## Lifecycle

1. **Create** when you start mid-task work that you might not finish in one sitting.
2. **Edit live** as you go — overwrite freely; this is not a log, it's a current-state snapshot.
3. **Delete or promote** when the work lands. If the rationale is worth keeping, move it into the design doc, the platform CLAUDE.md, or a commit message — not here.

A handoff note that has been sitting unchanged for more than a couple of weeks is either stale (delete it) or actually a design doc in disguise (promote it).

## Format

No template. Aim for under a screen. Cover what a stranger needs to pick up cold:

- **Goal** — what we're trying to do, in one line.
- **Status** — where we actually are right now.
- **Next** — the literal next action.
- **Open** — anything blocked or undecided.
- **Don't** — things already ruled out, so the next session doesn't re-explore them.

Anything more structured belongs in `docs/design/`.
