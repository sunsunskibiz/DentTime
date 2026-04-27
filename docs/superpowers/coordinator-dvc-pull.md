# Coordinator Prompt — DVC Raw Data Pull

Paste the block below as your first message in a new Claude Code session to run the orchestrated implementation.

---

```
Implement the DVC raw data pull plan task by task.
The plan is at docs/superpowers/plans/2026-04-27-dvc-raw-data-pull.md.
The spec is at docs/superpowers/specs/2026-04-27-dvc-raw-data-pull-design.md.

Execute tasks in order: 1, 2, 3, 4, 5, 6.

For each task:

1. Dispatch an implementer subagent (isolation: worktree) with:
   - The full task content from the plan (all steps and code)
   - The spec file for context
   - Instruction to follow the plan steps exactly, including running tests
     and committing at the end of each task

2. Once the implementer completes, get the git diff of the worktree and
   dispatch the denttime-dvc-critic subagent with:
   - The git diff
   - The task number (1–6)
   - Instruction: "Review this diff for Task <N>. Apply only Task <N> criteria."

3. If VERDICT: PASS — merge the worktree to the current branch, move to next task.

4. If VERDICT: FAIL — discard the worktree. Retry up to 3 total attempts for
   this task, passing the critic's ISSUES list back to the implementer each
   retry with: "Previous attempt failed critic review. Fix these issues and
   re-implement: <ISSUES>"

5. If still FAIL after 3 attempts — stop and escalate using this exact format:

ESCALATION — Task <N> failed after 3 attempts.

Critic issues from final attempt:
- <issue 1>
- <issue 2>

Please review and provide guidance. Options:
A) Give me a specific fix direction and I'll retry
B) Adjust the acceptance criteria for this task and move on
C) Skip this task for now

Special notes per task:
- Task 1: No test files — critic checks config and file structure only.
- Tasks 2–3: TDD order is enforced — tests must be committed before implementation.
- Tasks 4–5: TDD order is enforced — test changes must be committed before DAG changes.
- Task 6: docker/.env must NOT appear in the git diff (it is gitignored).
  If the implementer accidentally commits it, fail immediately and escalate.
```
