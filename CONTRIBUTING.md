# Contributing to InsightMesh Core

Thanks for the interest. A few things to know up front.

InsightMesh Core is a personal-but-public project. The constitution (`.specify/memory/constitution.md`) calls it a "personal tool" and means it: it is designed, scoped, and prioritized for a single user, built in public for transparency rather than for community throughput. Issues are welcome, PRs are accepted by prior discussion, and there is no SLA on either. If that posture fits what you want to engage with, read on.

---

## How to engage

**Issues: welcome.** Bug reports, feature requests, "I tried X and it broke" reports, and questions are all fine. File them at [aucontraire/insightmesh-core/issues](https://github.com/aucontraire/insightmesh-core/issues). For a bug, include the command you ran, the input you used (sanitized if private), what you expected, and what actually happened.

**Pull requests: discuss first.** Before writing code for a PR, open an issue describing what you intend to change and why. Two reasons:

1. The project has strong opinions about scope and architecture (see `.specify/memory/constitution.md` §Anti-Slop Engineering). A PR that would have been rejected for being out of scope is a frustrating wasted afternoon for both of us.
2. Direction may have shifted since the last commit. The story-roadmap in the maintainer's notes is private; a quick issue-level check ("are you still going in direction Y, or did Z change?") saves rework.

Trivial fixes (typos, broken links, dead imports, one-line bugs with an obvious fix) can skip the issue step. When in doubt, ask.

## AI-authored contributions

This project is built with heavy AI assistance and has no special policy against AI-authored PRs. The diff is judged on its merits, the same anti-slop bar applies regardless of who or what wrote it:

- Surgical changes that trace to a stated problem
- No speculative abstractions, no "while I was in here" cleanups
- No new files unless necessary (Anti-Slop "Minimal-Diff Principle")
- No new dependencies without justification (Anti-Slop "Dependency Discipline")
- Tests pass, types check, formatting clean (see Quality gates below)

You do not need to disclose AI authorship. You do need to actually understand the diff you are proposing, because review comments will assume you do.

---

## Development setup

### Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** for environment and dependency management (do not use bare `pip` or `venv`)
- **Git**
- **Obsidian** (for running the actual pipeline against a vault; not required for tests)
- A real Obsidian vault, or use the test-vault pattern (`~/Documents/InsightMesh-test-vault/` is a common convention)

### First-time setup

```bash
git clone https://github.com/aucontraire/insightmesh-core.git
cd insightmesh-core
uv sync --all-extras           # creates .venv, installs runtime + dev deps from uv.lock
uv run pre-commit install      # wires the class-registry regen hook + any future hooks
uv run pytest                  # confirm the test suite is green on your machine
```

If `.venv` ever gets out of sync (e.g., after pulling a `pyproject.toml` change), `uv sync --all-extras` rebuilds it from `uv.lock`. The lock file is the source of truth and is committed to git.

### Daily workflow

Every Python invocation runs through `uv run` so the project venv is used automatically:

- `uv run pytest` (never bare `pytest` or `.venv/bin/pytest`)
- `uv run mypy --strict src/`
- `uv run ruff check .` and `uv run ruff format .` (or `uv run black .`)
- `uv run python -m src.cli ...` to exercise the CLI directly

Adding a dependency: edit `pyproject.toml`, then `uv sync` (or `uv add <pkg>` for runtime, `uv add --dev <pkg>` for dev). New runtime dependencies need a Complexity Justification Table entry in the PR description unless they are already in the constitution's Project Standards list.

---

## Quality gates

These run locally and must pass before opening a PR. Pre-commit hooks catch most of them automatically:

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # formatting (or `uv run black --check .`)
uv run mypy --strict src/      # strict typing, no Any in public APIs
uv run pytest                  # full test suite
uv run mkdocs build --strict   # docs build cleanly (if you touched docs/)
```

### Strict typing rules

From the constitution §Project Standards Conventions:

- All Python classes that group fields together MUST be `pydantic.BaseModel` subclasses with `ConfigDict(strict=True)`. The `@dataclass` decorator, `typing.NamedTuple`, and `collections.namedtuple` are PROHIBITED for new data shapes in `src/` and `tests/`. Enforced mechanically by ruff `TID251` (see `pyproject.toml`).
- Strict typing throughout source and tests; no untyped `dict` / `Any` in public APIs.
- All async functions explicitly typed for return values.

### Class registry (anti-hallucination)

Before authoring, importing, instantiating, subclassing, or renaming any class, consult `.claude/class-registry.json`. The registry is AST-derived, auto-refreshed via pre-commit on Python file changes under `src/`, and gitignored. Helpers live in `.claude/tools/`:

- `generate_class_registry.py` rebuilds it manually if stale: `uv run python .claude/tools/generate_class_registry.py`
- `analyze_class_usage.py <ClassName>` enumerates every import / inheritance / instantiation / type-annotation site. Use BEFORE any rename.
- `validate_class_conflicts.py [--stats | --suggest <Name>]` reports duplicate-name conflicts and suggests renames.

Full rationale and triggers are documented in `CLAUDE.md` §0c and codified as a constitutional Anti-Slop Requirement (No Hallucinated Classes).

### Real-data smoke testing

Mocked tests catch correctness, but real-data smoke testing has caught every meaningful bug in Specs 004 and 005 (see commit messages). Before declaring a feature done, run it against an actual transcript and a real Obsidian vault. The `~/Documents/InsightMesh-test-vault/` pattern is a common convention. If you are adding a feature that touches the pipeline, please describe in the PR how you smoke-tested it.

---

## When to use SpecKit

The project uses [GitHub Spec Kit](https://github.com/github/spec-kit) for **feature-sized work**: anything that adds a user story, new functional requirements, or a meaningful architectural change. The workflow is:

```text
/speckit-specify "<feature description>"
/speckit-clarify
/speckit-plan
/speckit-tasks
/speckit-analyze
/speckit-implement
```

Specs land in `specs/NNN-<slug>/`. Recent examples: `001-chat-to-wiki-batch`, `005-page-provenance`.

**Skip SpecKit for:**

- Bug fixes
- Documentation changes
- Internal tooling (the class registry itself was a no-SpecKit port — pure infrastructure, no FRs)
- Single-file refactors
- Dependency bumps

If you are unsure whether your change is feature-sized, ask in the issue.

---

## Commit and PR conventions

### Branches

- SpecKit features: `NNN-<short-slug>` (e.g., `005-page-provenance`)
- Documentation: `docs/<short-slug>`
- Tooling / infrastructure: `tooling/<short-slug>`
- Bug fixes: `fix/<short-slug>`

Work off the latest `master`. The project does not use `develop` or release branches.

### Commit messages

Short imperative subject, optional scope prefix, focus the body on *why* over *what* (the diff already shows the what):

```text
docs: viewer plugin now in Obsidian community plugin browser (#8)
Spec 005: per-page provenance (v0.5.0) (#6)
tooling: class registry + auto-refresh hooks (anti-hallucination)
```

Avoid `git commit --amend` on commits you have already pushed. Avoid `--no-verify` to skip hooks (if a hook fails, fix the underlying issue). Never force-push to `master`.

### PR descriptions

A good PR description has:

- A 1-3 sentence summary of what changed and why
- A test plan (bullet list of how to verify the change works)
- A link to the issue that prompted it
- If the change adds a new file, a new dependency, or a new abstraction, the Complexity Justification Table from the constitution

For SpecKit features, link to the spec directory (`specs/NNN-<slug>/spec.md`) instead of restating the spec in the PR body.

### Stylistic conventions

- No em-dashes or en-dashes (—, –) in commit messages, PR descriptions, docs, or code comments. Use commas, periods, colons, parens, or arrows (->). This is a project-wide rule, not a personal preference; please respect it.
- No comments in code unless the *why* is non-obvious (CLAUDE.md §2). Well-named identifiers explain the what.
- No emojis in code or commit messages. Sparingly in docs / READMEs is fine where the project already uses them (status tables, callouts).

---

## Where the rules live

If something is unclear or you want to argue for a different approach, the precedence order is:

1. **`.specify/memory/constitution.md`** — project law. Architectural principles, quality gates, governance. Amendments follow the procedure in §Governance and require a version bump.
2. **`CLAUDE.md`** — operational guidance for AI assistants working on the project (and a useful read for human contributors). When in conflict with the constitution, constitution wins.
3. **Specs in `specs/NNN-<slug>/`** — feature-level decisions, with `spec.md` (functional requirements), `plan.md` (implementation strategy), and `tasks.md` (task breakdown).
4. **PR-level discussion** — for everything not covered above.

Disagreements about a constitutional principle should be raised as an issue, not in a PR. The amendment procedure exists for that.

---

## License

By contributing, you agree your contribution is licensed under [AGPL-3.0](LICENSE), the same license as the rest of the project. If you run a modified version of InsightMesh Core as a network service, AGPL requires you to make your changes available to its users. There is no contributor license agreement to sign.
