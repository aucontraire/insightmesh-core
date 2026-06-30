#!/usr/bin/env python3
"""
Class Registry Generator for insightmesh-core

Generates a JSON registry of every class under src/ via AST parsing. No imports,
no runtime side effects, deterministic.

Output: .claude/class-registry.json, keyed by class NAME with a list of
definitions per name (so duplicate-name cases surface naturally rather than
getting silently collapsed). Each definition carries file path + line number +
parent classes + class type (pydantic_model / orm_model / typed_dict /
dataclass / enum / protocol / class) + method signatures + field shapes.

Anti-hallucination use case: subagents and the interactive operator can grep
the registry to answer "does this class exist? what's its signature? where is
it defined?" deterministically instead of guessing or fan-grepping the tree.

Project-stack notes:
  - Pydantic v2 BaseModel + pydantic-settings BaseSettings tagged pydantic_model
  - SQLAlchemy 2.0 `Base` subclasses tagged orm_model (defensive: the project
    has no ORM today, but the check is zero-cost and surfaces an unintended
    drift if one ever appears)
  - Default scan dir: src/ (flat layout)

Known limitation — transitive Pydantic inheritance:
  Classification only inspects the immediate parent's short name, so a class
  that inherits from a Pydantic model (rather than directly from BaseModel/
  BaseSettings) is tagged "class", not "pydantic_model". `parent_classes` still
  points at the real base, so following that pointer recovers the truth, and
  the file/field/method extraction is unaffected. Today the only case is
  `CheckpointRecordRead` (deliberately permissive `extra="allow"` per Spec 005
  FR-002). Fix when a second pattern appears: add a two-pass classifier that
  builds a name→type map, then resolves parents transitively.


Usage:
    uv run python .claude/tools/generate_class_registry.py
    uv run python .claude/tools/generate_class_registry.py --include-tests
    uv run python .claude/tools/generate_class_registry.py --scan-dir src --scan-dir tests
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── git SHA (stdlib only) ────────────────────────────────────────────────────


def _git_version(default: str = "unknown") -> str:
    """Return the short git SHA, or `default` if not in a git repo."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return sha or default
    except (subprocess.CalledProcessError, FileNotFoundError):
        return default


# ── data class for per-class metadata ────────────────────────────────────────


@dataclass
class ClassInfo:
    name: str
    type: str  # class | pydantic_model | orm_model | typed_dict | dataclass | enum | protocol
    parent_classes: list[str]
    file_path: str
    line_number: int
    module: str
    file_type: str  # main | test | factory | script
    methods: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    docstring: str | None
    decorators: list[str]


# ── AST visitor: extracts ClassInfo per ClassDef ─────────────────────────────


class ClassExtractor(ast.NodeVisitor):
    """AST visitor: walks ClassDef nodes; emits a ClassInfo per class."""

    # Pydantic v2 base classes. BaseSettings is from pydantic-settings.
    _PYDANTIC_BASES = {"BaseModel", "BaseSettings"}

    # SQLAlchemy 2.0 declarative base. The project doesn't use an ORM today;
    # the check is defensive and surfaces drift if one is ever added.
    _ORM_BASES = {"Base"}

    def __init__(self, file_path: str, module_name: str, file_type: str) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.file_type = file_type
        self.classes: list[ClassInfo] = []

    def extract(self) -> list[ClassInfo]:
        try:
            with open(self.file_path, encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content)
            self.visit(tree)
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"⚠️  could not parse {self.file_path}: {exc}", file=sys.stderr)
        return self.classes

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(self._build_class_info(node))
        self.generic_visit(node)

    # ── per-class extractors ─────────────────────────────────────────────────

    def _build_class_info(self, node: ast.ClassDef) -> ClassInfo:
        parent_classes = [self._render_expr(base) for base in node.bases]
        decorators = [self._render_expr(dec) for dec in node.decorator_list]
        return ClassInfo(
            name=node.name,
            type=self._determine_class_type(parent_classes, decorators),
            parent_classes=parent_classes,
            file_path=self.file_path,
            line_number=node.lineno,
            module=self.module_name,
            file_type=self.file_type,
            methods=self._extract_methods(node),
            fields=self._extract_fields(node),
            docstring=ast.get_docstring(node),
            decorators=decorators,
        )

    def _determine_class_type(self, parents: list[str], decorators: list[str]) -> str:
        """Classify the class by its parent + decorators.

        Precedence: decorators (dataclass) > parents (typed_dict/pydantic/orm/enum/protocol)
        > fallback 'class'.
        """
        for dec in decorators:
            if "dataclass" in dec.lower():
                return "dataclass"

        for parent in parents:
            short = parent.split(".")[-1]  # e.g. "BaseModel" from "pydantic.BaseModel"
            if short == "TypedDict":
                return "typed_dict"
            if short in self._PYDANTIC_BASES:
                return "pydantic_model"
            if short in self._ORM_BASES:
                return "orm_model"
            if short in {"Enum", "StrEnum", "IntEnum"}:
                return "enum"
            if short == "Protocol":
                return "protocol"

        return "class"

    def _extract_methods(self, node: ast.ClassDef) -> list[dict[str, Any]]:
        methods: list[dict[str, Any]] = []
        for item in node.body:
            if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            methods.append(
                {
                    "name": item.name,
                    "signature": self._method_signature_string(item),
                    "parameters": self._method_parameters(item),
                    "return_type": self._render_annotation(item.returns) if item.returns else None,
                    "is_property": self._has_decorator(item, "property"),
                    "is_classmethod": self._has_decorator(item, "classmethod"),
                    "is_staticmethod": self._has_decorator(item, "staticmethod"),
                    "is_async": isinstance(item, ast.AsyncFunctionDef),
                    "line_number": item.lineno,
                }
            )
        return methods

    def _extract_fields(self, node: ast.ClassDef) -> list[dict[str, Any]]:
        """Extract annotated + assigned class-level attributes.

        For Pydantic models, picks up field declarations + their defaults.
        For SQLAlchemy ORM models (if/when added), picks up `Mapped[T]` columns.
        """
        fields: list[dict[str, Any]] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                field_info = {
                    "name": item.target.id,
                    "type": self._render_annotation(item.annotation),
                    "default_value": self._render_expr(item.value) if item.value else None,
                    "is_required": item.value is None,
                }
                if (
                    isinstance(item.value, ast.Call)
                    and isinstance(item.value.func, ast.Name)
                    and item.value.func.id == "Field"
                ):
                    desc = self._extract_field_description(item.value)
                    if desc:
                        field_info["description"] = desc
                fields.append(field_info)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        fields.append(
                            {
                                "name": target.id,
                                "type": "Any",
                                "default_value": self._render_expr(item.value),
                                "is_required": False,
                            }
                        )
        return fields

    # ── helpers ──────────────────────────────────────────────────────────────

    def _method_signature_string(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        parts: list[str] = []
        defaults = node.args.defaults
        num_positional = len(node.args.args)
        num_defaults = len(defaults)

        for i, arg in enumerate(node.args.args):
            s = arg.arg
            if arg.annotation:
                s += f": {self._render_annotation(arg.annotation)}"
            default_index = i - (num_positional - num_defaults)
            if default_index >= 0:
                s += f" = {self._render_expr(defaults[default_index])}"
            parts.append(s)

        if node.args.vararg:
            s = f"*{node.args.vararg.arg}"
            if node.args.vararg.annotation:
                s += f": {self._render_annotation(node.args.vararg.annotation)}"
            parts.append(s)
        elif node.args.kwonlyargs:
            parts.append("*")

        kw_defaults = node.args.kw_defaults or []
        for i, arg in enumerate(node.args.kwonlyargs):
            s = arg.arg
            if arg.annotation:
                s += f": {self._render_annotation(arg.annotation)}"
            if i < len(kw_defaults) and kw_defaults[i] is not None:
                s += f" = {self._render_expr(kw_defaults[i])}"
            parts.append(s)

        if node.args.kwarg:
            s = f"**{node.args.kwarg.arg}"
            if node.args.kwarg.annotation:
                s += f": {self._render_annotation(node.args.kwarg.annotation)}"
            parts.append(s)

        return f"({', '.join(parts)})"

    def _method_parameters(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
        params: list[dict[str, Any]] = []
        defaults = node.args.defaults
        num_positional = len(node.args.args)
        num_defaults = len(defaults)

        for i, arg in enumerate(node.args.args):
            default_index = i - (num_positional - num_defaults)
            params.append(
                {
                    "name": arg.arg,
                    "type": self._render_annotation(arg.annotation) if arg.annotation else "Any",
                    "has_type_annotation": arg.annotation is not None,
                    "kind": "positional",
                    "has_default": default_index >= 0,
                    "default_value": self._render_expr(defaults[default_index]) if default_index >= 0 else None,
                    "position": i,
                }
            )
        if node.args.vararg:
            params.append(
                {
                    "name": node.args.vararg.arg,
                    "type": self._render_annotation(node.args.vararg.annotation)
                    if node.args.vararg.annotation
                    else "Any",
                    "has_type_annotation": node.args.vararg.annotation is not None,
                    "kind": "varargs",
                    "has_default": False,
                    "default_value": None,
                    "position": len(params),
                }
            )

        kw_defaults = node.args.kw_defaults or []
        for i, arg in enumerate(node.args.kwonlyargs):
            has_default = i < len(kw_defaults) and kw_defaults[i] is not None
            params.append(
                {
                    "name": arg.arg,
                    "type": self._render_annotation(arg.annotation) if arg.annotation else "Any",
                    "has_type_annotation": arg.annotation is not None,
                    "kind": "keyword_only",
                    "has_default": has_default,
                    "default_value": self._render_expr(kw_defaults[i]) if has_default else None,
                    "position": len(params),
                }
            )
        return params

    def _has_decorator(self, node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
        for dec in node.decorator_list:
            rendered = self._render_expr(dec)
            if rendered == name or rendered.endswith(f".{name}"):
                return True
        return False

    def _extract_field_description(self, call: ast.Call) -> str | None:
        """Surface `Field(description='...')` from a Pydantic field declaration."""
        for kw in call.keywords:
            if kw.arg == "description" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return None

    def _render_annotation(self, node: ast.AST | None) -> str:
        if node is None:
            return "None"
        return self._render_expr(node)

    def _render_expr(self, node: ast.AST | None) -> str:
        """Best-effort source-rendering of an AST node.

        Uses ast.unparse (Python 3.9+) for fidelity. Falls back to ast.dump on
        unparse failure (defensive — shouldn't happen on well-formed code).
        """
        if node is None:
            return "None"
        try:
            return ast.unparse(node)
        except Exception:
            return ast.dump(node)


# ── registry orchestration ────────────────────────────────────────────────────


class ClassRegistryGenerator:
    """Walks scan_directories, runs the AST extractor on each `.py`, builds
    the JSON registry."""

    _EXCLUDED = (
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".venv",
        "venv",
        "env",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "site",  # mkdocs build output
        "dist",
        "build",
    )

    def __init__(self, scan_directories: list[str], include_tests: bool = False) -> None:
        self.scan_directories = scan_directories
        self.include_tests = include_tests

    def generate(self) -> dict[str, Any]:
        all_classes: list[ClassInfo] = []
        for d in self.scan_directories:
            all_classes.extend(self._scan_directory(d))

        by_name: dict[str, list[ClassInfo]] = defaultdict(list)
        for c in all_classes:
            by_name[c.name].append(c)

        duplicates = sum(1 for instances in by_name.values() if len(instances) > 1)
        now = datetime.now(UTC).isoformat()

        return {
            "metadata": {
                "generated_at": now,
                "updated_at": now,
                "git_version": _git_version(),
                "total_classes": len(all_classes),
                "duplicates_found": duplicates,
                "scan_directories": self.scan_directories,
                "excluded_patterns": list(self._EXCLUDED),
            },
            "classes": {
                name: [self._class_to_dict(c) for c in instances] for name, instances in sorted(by_name.items())
            },
        }

    def _scan_directory(self, directory: str) -> list[ClassInfo]:
        path = Path(directory)
        if not path.exists():
            print(f"⚠️  directory does not exist: {directory}", file=sys.stderr)
            return []

        results: list[ClassInfo] = []
        for py in path.rglob("*.py"):
            if any(part in self._EXCLUDED for part in py.parts):
                continue
            file_type = self._determine_file_type(py)
            if file_type == "test" and not self.include_tests:
                continue
            module_name = self._module_name(py, directory)
            extractor = ClassExtractor(str(py), module_name, file_type)
            results.extend(extractor.extract())
        return results

    @staticmethod
    def _determine_file_type(path: Path) -> str:
        s = str(path).lower()
        if "/tests/" in s or path.name.startswith("test_") or path.name.endswith("_test.py"):
            return "test"
        if "/factories/" in s or "factory" in path.name:
            return "factory"
        if path.name in {"__main__.py", "main.py", "cli.py"}:
            return "script"
        return "main"

    @staticmethod
    def _module_name(path: Path, base: str) -> str:
        try:
            rel = path.relative_to(Path(base))
        except ValueError:
            return path.stem
        parts = list(rel.parts[:-1])
        if rel.stem != "__init__":
            parts.append(rel.stem)
        return ".".join(p for p in parts if p)

    @staticmethod
    def _class_to_dict(c: ClassInfo) -> dict[str, Any]:
        return {
            "name": c.name,
            "type": c.type,
            "parent_classes": c.parent_classes,
            "file_path": c.file_path,
            "line_number": c.line_number,
            "module": c.module,
            "file_type": c.file_type,
            "methods": c.methods,
            "fields": c.fields,
            "docstring": c.docstring,
            "decorators": c.decorators,
        }


# ── entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate class registry for insightmesh-core")
    parser.add_argument(
        "--output",
        "-o",
        default=".claude/class-registry.json",
        help="Output JSON path (default: .claude/class-registry.json)",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test files in the scan (default: skip)",
    )
    parser.add_argument(
        "--scan-dir",
        action="append",
        default=None,
        help="Directory to scan (repeatable). Default: src",
    )
    args = parser.parse_args()

    if args.scan_dir:
        scan_dirs = args.scan_dir
    elif args.include_tests:
        scan_dirs = ["src", "tests"]
    else:
        scan_dirs = ["src"]

    registry = ClassRegistryGenerator(scan_dirs, args.include_tests).generate()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(registry, indent=2, ensure_ascii=False))

    meta = registry["metadata"]
    print("✅ class registry generated")
    print(f"   total classes:    {meta['total_classes']}")
    print(f"   duplicate names:  {meta['duplicates_found']}")
    print(f"   scanned dirs:     {', '.join(meta['scan_directories'])}")
    print(f"   git version:      {meta['git_version']}")
    print(f"   output:           {args.output}")

    if meta["duplicates_found"] > 0:
        print("\n⚠️  duplicate class names (first 20):")
        shown = 0
        for name, instances in registry["classes"].items():
            if len(instances) <= 1:
                continue
            print(f"  • {name}: {len(instances)} instances")
            for inst in instances:
                print(f"    - {inst['file_path']}:{inst['line_number']} ({inst['type']})")
            shown += 1
            if shown >= 20:
                remaining = meta["duplicates_found"] - shown
                if remaining > 0:
                    print(f"  ... and {remaining} more (see {args.output} for full list)")
                break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
