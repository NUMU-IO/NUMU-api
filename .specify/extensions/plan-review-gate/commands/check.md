---
description: "Check that spec.md and plan.md have been merged via MR/PR before allowing task generation"
---

# Plan Review Gate — Pre-Tasks Check

This is a mandatory `before_tasks` gate. It blocks `/speckit.tasks` unless the spec and plan artifacts have already been committed and merged (i.e. they are not new files in source control).

## Steps

### 1. Locate Feature Artifacts

Run `.specify/scripts/bash/check-prerequisites.sh --json` from the repo root and parse the output to determine the FEATURE_DIR. If the script is not available, search for the most recent `specs/*/plan.md` relative to the repo root.

Verify that both `spec.md` and `plan.md` exist in FEATURE_DIR. If either is missing, ERROR and stop:

```
BLOCKED: spec.md and plan.md must both exist before running /speckit.tasks.
Run /speckit.specify and /speckit.plan first.
```

### 2. Check Git Status of Artifacts

Determine the default branch name:

```bash
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@'
```

If that fails, fall back to `main`, then `master`.

For each artifact (`spec.md` and `plan.md`), check whether the file exists on the default branch:

```bash
git cat-file -e origin/{default_branch}:{relative_path_to_spec_md} 2>/dev/null
git cat-file -e origin/{default_branch}:{relative_path_to_plan_md} 2>/dev/null
```

A non-zero exit code means the file does NOT exist on the default branch — it is a new file that has not been merged.

Also check for uncommitted changes to the artifacts:

```bash
git status --porcelain -- {FEATURE_DIR}/spec.md {FEATURE_DIR}/plan.md
```

### 3. Gate Decision

**If BOTH spec.md and plan.md exist on the default branch (merged) and have no uncommitted changes:**

```
Plan review gate: PASSED
spec.md and plan.md are present on {default_branch} — proceeding to task generation.
```

Allow `/speckit.tasks` to continue.

**If EITHER file is new (not on default branch) OR has uncommitted changes:**

Report exactly which files are blocking and stop execution. Do NOT allow task generation to proceed.

```
BLOCKED: spec.md and plan.md must be merged before running /speckit.tasks.

The following files have not been merged to {default_branch}:
- {list each file that is new or has uncommitted changes}

To unblock:
1. Commit the spec artifacts:  git add {FEATURE_DIR}/ && git commit -m "Add spec and plan"
2. Push and create a merge request for review
3. Once reviewed and merged, run /speckit.tasks again

This gate ensures implementation plans are reviewed before task generation begins.
```

**IMPORTANT**: When blocked, you MUST stop execution entirely. Do NOT continue with task generation. Do NOT offer to skip this check unless the user explicitly passes `--skip-review`. The whole point of this extension is to prevent /speckit.tasks from running until the plan has been reviewed and merged.

### 4. Override Check

If the user passes `--skip-review` in their `/speckit.tasks` arguments (via `$ARGUMENTS`), skip the git checks and allow task generation to proceed. Print a warning:

```
Plan review gate: SKIPPED (--skip-review)
Warning: proceeding without a merged plan review. This is not recommended for production features.
```

## Key Rules

- This is a hard gate by default — it blocks task generation unless spec.md and plan.md are merged
- The only override is `--skip-review` passed explicitly by the user
- Do NOT generate tasks if the check fails and no override is present
- Do NOT suggest `--skip-review` when blocked — only honour it if the user provides it themselves
- The check is based on git state, not user confirmation
- Only spec.md and plan.md are checked (other artifacts like research.md are not required)
