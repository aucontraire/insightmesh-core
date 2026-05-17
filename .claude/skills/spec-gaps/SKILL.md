---
name: spec-gaps
description: Read the active checklist, find unchecked gaps and inconsistencies, and draft spec updates to address them
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

Optional: the user can specify a checklist filename (e.g., "agent-pipeline") or "all" to process all checklists. If empty, process the most recent checklist in the active feature's `checklists/` directory.

## Workflow

1. **Find the active feature**: Run `.specify/scripts/bash/check-prerequisites.sh --json --paths-only` to get `FEATURE_DIR`.

2. **Load the checklist**:
   - Read the specified checklist (or most recent) from `FEATURE_DIR/checklists/`
   - Parse all unchecked items (`- [ ]`)
   - Categorize by marker: `[Gap]`, `[Consistency]`, `[Ambiguity]`, `[Clarity]`, `[Completeness]`, `[Coverage]`

3. **Load the spec and plan**:
   - Read `FEATURE_DIR/spec.md`
   - Read `FEATURE_DIR/plan.md` (if exists)
   - Read `FEATURE_DIR/data-model.md` (if exists)
   - Read `FEATURE_DIR/contracts/` (if exists)

4. **For each unchecked gap item**, draft a resolution:
   - **[Gap]**: Draft the missing requirement text (new FR, edge case, or acceptance scenario)
   - **[Consistency]**: Identify the conflicting statements and propose a canonical resolution
   - **[Ambiguity]**: Propose specific, measurable replacement text
   - **[Clarity]**: Propose a clearer definition with concrete criteria
   - **[Completeness]**: Draft the missing specification section or detail
   - **[Coverage]**: Draft the missing scenario or edge case

5. **Present all proposed changes** to the user in a structured format:

   ```markdown
   ## Proposed Spec Updates

   ### CHK0XX — [checklist item summary]
   **Issue**: [what's missing or unclear]
   **Proposed text**: [exact text to add/replace in the spec]
   **Target section**: [which spec section to update]
   ```

6. **Wait for user approval**:
   - User can approve all: "apply all"
   - User can approve selectively: "apply CHK011, CHK014, CHK018"
   - User can skip: "skip" or "none"
   - User can modify: provide alternative text for specific items

7. **Apply approved changes**:
   - Update `spec.md` with approved text
   - Check off the resolved items in the checklist (`- [x]`)

8. **Cross-document consistency pass** (MANDATORY after applying changes):
   After updating spec.md, systematically check ALL other documents in `FEATURE_DIR/` for inconsistencies introduced by the changes:
   - **`data-model.md`**: Do entity fields, constraints, enums, or relationships still match the spec? If the spec added new FRs, added fields, changed enums, or modified formulas — update the data model to match.
   - **`research.md`**: Do technical decisions still align with spec changes? Verify research decisions aren't contradicted.
   - **`plan.md`**: Does the project structure, technical context, or constitution check still align? Update if spec changes affect architecture.
   - **`quickstart.md`**: Do usage examples still match the contracts?

   For each document, either:
   - Update it to match the spec (if inconsistency found)
   - Confirm it's already consistent (note in report)

9. **Report**:
   - N gaps addressed, N skipped, N remaining
   - List of ALL files updated (not just spec.md)
   - Cross-document consistency results (which files were checked, which needed updates)
   - Suggest re-running `/speckit-checklist` if significant changes were made
