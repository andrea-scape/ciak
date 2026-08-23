# Project Instructions — Ciak (Trakt client)

Operational guide: read `~/Desktop/ciak-workflow.md` before any code, test, or deploy work.

## DELEGATION — REQUIRED IN THIS PROJECT

This repo has a 185-test suite, a deep `src/` tree, and verbose build logs. Inline
exploration floods the context window — delegate instead:

- File/code searches, "where is X", multi-file reads → dispatch `explore`
- Multi-step analysis, cross-file test-failure triage → dispatch `general`
- Dispatch prompts must contain exact file paths; subagents report in ≤5 lines.
- Run tests/builds/deploy inline only when the result is needed for the next decision.
