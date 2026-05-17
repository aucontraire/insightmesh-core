---
name: reconcile
description: Resolve issues found by /speckit-analyze — apply remediations across spec, plan, tasks, data model, and contracts
user-invocable: true
disable-model-invocation: true
metadata:
  author: insightmesh-core
  source: custom
---

## User Input

```text
$ARGUMENTS
```

Optional: the user can specify issue IDs to address (e.g., "I1, C1, A1") or a severity filter ("high", "medium", "all"). If empty, address all HIGH and MEDIUM issues by default. LOW issues are included only if explicitly requested.

## Prerequisites

This skill MUST be run after `/speckit-analyze` has produced a report. If no recent analyze report exists in the conversation, instruct the user to run `/speckit-analyze` first.

## Workflow

1. **Find the active feature**: Run `.specify/scripts/bash/check-prerequisites.sh --json --paths-only` to get `FEATURE_DIR`.

2. **Load all artifacts**:
   - `FEATURE_DIR/spec.md` (required)
   - `FEATURE_DIR/plan.md` (required)
   - `FEATURE_DIR/tasks.md` (required)
   - `FEATURE_DIR/data-model.md` (if exists)
   - `FEATURE_DIR/contracts/` (if exists)
   - `FEATURE_DIR/research.md` (if exists)
   - `FEATURE_DIR/quickstart.md` (if exists)
   - `.specify/memory/constitution.md` (for constitution alignment issues)

3. **Collect the issues** from the most recent `/speckit-analyze` report in the conversation. Parse each finding by:
   - **ID** (e.g., I1, C1, A1, D1, K1)
   - **Category** (Inconsistency, Coverage Gap, Ambiguity, Duplication, Constitution)
   - **Severity** (CRITICAL, HIGH, MEDIUM, LOW)
   - **Location(s)** (which files and sections are affected)
   - **Summary** and **Recommendation**

4. **Filter issues** based on user input:
   - Specific IDs: only address those
   - Severity filter: "high" = CRITICAL + HIGH, "medium" = CRITICAL + HIGH + MEDIUM, "all" = everything
   - Default (no input): CRITICAL + HIGH + MEDIUM

5. **For each issue, draft a concrete remediation**:

   ```markdown
   ### Issue [ID] — [Summary] ([Severity])

   **Problem**: [What's wrong, citing specific lines/sections]

   **Changes**:
   - **[filename]**: [exact text to change — old → new]
   - **[filename]**: [exact text to add/remove]

   **Rationale**: [Why this fix is correct]
   ```

   Remediation rules by category:
   - **Inconsistency**: Identify the canonical source of truth, update all other documents to match
   - **Coverage Gap**: Add missing tasks to tasks.md (with correct IDs, [P] markers, [Story] labels, file paths), add missing tests
   - **Ambiguity**: Propose specific, measurable replacement text in the spec
   - **Duplication**: Merge into the clearer version, remove the duplicate
   - **Constitution**: Adjust spec/plan/tasks to comply — never modify the constitution

6. **Present all proposed changes** to the user, grouped by severity (CRITICAL first, then HIGH, then MEDIUM, then LOW).

7. **Wait for user approval**:
   - `apply all` — apply all proposed changes
   - `apply I1, C1, A1` — apply specific issues only
   - `skip` — skip all
   - User can provide alternative text for any item

8. **Apply approved changes**:
   - Edit each affected file with the approved text
   - Renumber task IDs in tasks.md if tasks were added or removed (maintain sequential order)
   - Update dependency graphs in tasks.md if task ordering changed

9. **Cross-document consistency pass** (MANDATORY after applying changes):
   After applying changes, systematically verify ALL documents in `FEATURE_DIR/` remain consistent:
   - **spec.md ↔ data-model.md**: Entity fields, enums, constraints still match?
   - **spec.md ↔ tasks.md**: Every FR has at least one task? Task descriptions reference correct FRs?
   - **plan.md ↔ tasks.md**: Project structure, file paths, tech stack references aligned?
   - **data-model.md ↔ tasks.md**: Model file paths, field names, relationships consistent?
   - **research.md ↔ plan.md**: Technical decisions still align with architecture?
   - **quickstart.md**: Usage examples match expected interfaces?

   For each inconsistency found during this pass:
   - Fix it immediately (don't defer)
   - Note it in the report

10. **Report**:
    - Issues addressed: N (by severity)
    - Issues skipped: N
    - Files modified: list all
    - Cross-document consistency: results of pass (which files checked, which needed additional fixes)
    - If any CRITICAL or HIGH issues remain unresolved, warn that `/speckit-implement` should wait
    - Suggest re-running `/speckit-analyze` if significant structural changes were made (e.g., tasks reordered, new FRs added)
