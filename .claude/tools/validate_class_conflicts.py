#!/usr/bin/env python3
"""
Class Conflict Validator for insightmesh-core

Reads the JSON registry produced by generate_class_registry.py and reports
duplicate-name conflicts. For each conflict, suggests rename candidates based
on the duplicate's module stem (path-agnostic — works for any layout) and its
class type.

The project has zero duplicates today; the validator exists to flag them the
moment one appears, with actionable rename suggestions rather than a bare
"conflict detected" message.

Usage:
    uv run python .claude/tools/validate_class_conflicts.py             # report all conflicts
    uv run python .claude/tools/validate_class_conflicts.py --stats     # registry stats
    uv run python .claude/tools/validate_class_conflicts.py --class-name WikiPageDraft
    uv run python .claude/tools/validate_class_conflicts.py --suggest WikiPageDraft
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# ── suggestion rules ─────────────────────────────────────────────────────────


def _path_suggestions(class_name: str, file_path: str) -> list[str]:
    """Suggest renames informed by the containing module stem.

    Generic: works for any layout. Example — class Foo in src/checkpoint.py
    and src/history.py yields FooCheckpoint and FooHistory respectively. Tune
    with project-specific rules here when distinct duplicate patterns emerge.
    """
    p = file_path.replace("\\", "/")
    stem = Path(p).stem
    if not stem or stem in {"__init__", "__main__"}:
        return []
    # Suggest <ClassName><PascalStem> — e.g. WikiPage + checkpoint → WikiPageCheckpoint.
    pascal = "".join(part.capitalize() for part in stem.replace("-", "_").split("_") if part)
    if not pascal or pascal.lower() == class_name.lower():
        return []
    return [f"{class_name}{pascal}"]


def _type_suggestions(class_name: str, class_type: str) -> list[str]:
    return {
        "pydantic_model": [f"{class_name}Model"],
        "orm_model": [f"{class_name}ORM"],
        "typed_dict": [f"{class_name}Dict"],
        "dataclass": [f"{class_name}Data"],
        "enum": [f"{class_name}Enum"],
        "protocol": [f"{class_name}Protocol"],
    }.get(class_type, [])


# ── validator ────────────────────────────────────────────────────────────────


class ClassConflictValidator:
    def __init__(self, registry_path: str) -> None:
        self.registry_path = registry_path
        self.registry: dict[str, Any] | None = None

    def load(self) -> bool:
        try:
            self.registry = json.loads(Path(self.registry_path).read_text(encoding="utf-8"))
            return True
        except FileNotFoundError:
            print(f"❌ registry file not found: {self.registry_path}")
            print("   run `uv run python .claude/tools/generate_class_registry.py` first.")
            return False
        except json.JSONDecodeError as exc:
            print(f"❌ invalid JSON in registry: {exc}")
            return False

    # ── public ops ───────────────────────────────────────────────────────────

    def validate_all(self) -> bool:
        assert self.registry is not None
        classes = self.registry.get("classes", {})
        conflicts = [(n, instances) for n, instances in classes.items() if len(instances) > 1]

        print("Checking for class naming conflicts...")
        print("=" * 60)

        if not conflicts:
            print("✅ no class naming conflicts found")
            print(f"   total classes checked: {self.registry['metadata']['total_classes']}")
            return True

        for name, instances in conflicts:
            self._print_conflict(name, instances)

        print(
            f"\n⚠️  {len(conflicts)} duplicate class names "
            f"({self.registry['metadata']['duplicates_found']} per registry metadata)"
        )
        print("   use --suggest <NAME> to get rename candidates for a specific conflict.")
        return False

    def validate_class_name(self, class_name: str) -> bool:
        assert self.registry is not None
        classes = self.registry.get("classes", {})
        if class_name not in classes:
            print(f"✅ class '{class_name}' not found — name is available")
            return True
        instances = classes[class_name]
        if len(instances) == 1:
            print(f"   class '{class_name}' exists once:")
            self._print_class_info(instances[0])
            return True
        print(f"❌ class '{class_name}' has naming conflicts:")
        self._print_conflict(class_name, instances)
        return False

    def suggest_resolution(self, class_name: str) -> None:
        assert self.registry is not None
        classes = self.registry.get("classes", {})
        if class_name not in classes or len(classes[class_name]) <= 1:
            print(f"   no conflict to resolve for '{class_name}'.")
            return

        instances = classes[class_name]
        print(f"\nSuggested resolutions for '{class_name}':")
        print("-" * 60)

        for i, inst in enumerate(instances, start=1):
            file_path = inst["file_path"]
            class_type = inst["type"]
            parents = inst.get("parent_classes", [])
            suggestions = _path_suggestions(class_name, file_path) + _type_suggestions(class_name, class_type)
            seen: set[str] = set()
            unique = [s for s in suggestions if not (s in seen or seen.add(s))]
            print(f"  {i}. {file_path}:{inst['line_number']}")
            print(f"     type: {class_type}, parents: {parents}")
            print(f"     suggestions: {', '.join(unique[:4]) if unique else '(no rules matched)'}")
            print()

    def stats(self) -> None:
        assert self.registry is not None
        meta = self.registry["metadata"]
        classes = self.registry.get("classes", {})

        print("Class Registry Statistics")
        print("=" * 40)
        print(f"generated:    {meta['generated_at']}")
        print(f"git version:  {meta['git_version']}")
        print(f"total classes:{meta['total_classes']}")
        print(f"unique names: {len(classes)}")
        print(f"duplicates:   {meta['duplicates_found']}")
        print(f"scan dirs:    {', '.join(meta['scan_directories'])}")

        type_counts: dict[str, int] = {}
        file_type_counts: dict[str, int] = {}
        for instances in classes.values():
            for inst in instances:
                type_counts[inst["type"]] = type_counts.get(inst["type"], 0) + 1
                file_type_counts[inst["file_type"]] = file_type_counts.get(inst["file_type"], 0) + 1

        print("\nclass type breakdown:")
        for t, c in sorted(type_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {t}: {c}")
        print("\nfile type breakdown:")
        for t, c in sorted(file_type_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {t}: {c}")

    # ── private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _print_conflict(class_name: str, instances: list[dict[str, Any]]) -> None:
        print(f"\n❌ CONFLICT: '{class_name}' ({len(instances)} instances)")
        for i, inst in enumerate(instances, start=1):
            print(f"  {i}. {inst['file_path']}:{inst['line_number']}")
            print(f"     type: {inst['type']}")
            if inst.get("parent_classes"):
                print(f"     parents: {', '.join(inst['parent_classes'])}")
            doc = (inst.get("docstring") or "").strip().split("\n", 1)[0].strip()
            if doc:
                trimmed = doc[:60] + ("..." if len(doc) > 60 else "")
                print(f"     doc: {trimmed}")

    @staticmethod
    def _print_class_info(inst: dict[str, Any]) -> None:
        print(f"  {inst['file_path']}:{inst['line_number']}")
        print(f"  type:    {inst['type']}")
        if inst.get("parent_classes"):
            print(f"  parents: {', '.join(inst['parent_classes'])}")
        if inst.get("methods"):
            print(f"  methods: {len(inst['methods'])}")
        doc = (inst.get("docstring") or "").strip().split("\n", 1)[0].strip()
        if doc:
            print(f"  doc:     {doc}")


# ── entry point ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate class naming conflicts for insightmesh-core")
    parser.add_argument(
        "--registry",
        "-r",
        default=".claude/class-registry.json",
        help="Path to class registry JSON (default: .claude/class-registry.json)",
    )
    parser.add_argument("--class-name", "-c", help="Check a specific class name for conflicts")
    parser.add_argument("--suggest", "-s", help="Show rename suggestions for a conflicted class name")
    parser.add_argument("--stats", action="store_true", help="Show registry statistics")
    args = parser.parse_args()

    validator = ClassConflictValidator(args.registry)
    if not validator.load():
        return 1

    if args.stats:
        validator.stats()
        return 0

    if args.class_name:
        ok = validator.validate_class_name(args.class_name)
        if args.suggest:
            validator.suggest_resolution(args.class_name)
        return 0 if ok else 1

    if args.suggest:
        validator.suggest_resolution(args.suggest)
        return 0

    return 0 if validator.validate_all() else 1


if __name__ == "__main__":
    raise SystemExit(main())
