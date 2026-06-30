#!/usr/bin/env python3
"""
Class Usage Analyzer for insightmesh-core

Walks every Python file in the configured scan paths and emits every place a
target class name is mentioned, classified by usage type:

  - import          : `from X import Foo` / `import Foo` / aliased imports
  - inheritance     : `class Bar(Foo):` — Foo appears in a class's bases
  - instantiation   : `Foo(...)` — call whose callee is the name Foo
  - type_annotation : Foo appears inside any annotation expression
                      (AnnAssign target annotation, function arg/return annotation,
                      nested in Optional[Foo] / list[Foo] / Union[Foo, X], etc.)
  - reference       : any other bare-name occurrence (assignment, isinstance, etc.)

Use this BEFORE a rename to enumerate every file + line that must change.

Implementation note: a pre-pass collects the IDs of every AST node living
inside an annotation subtree, so Names nested in `Optional[Foo]` / `list[Foo]`
classify correctly as type_annotation rather than plain reference. Specialized
visitors claim Names that are import targets, inheritance bases, and call
callees; `visit_Name` runs last and skips any Name whose id() has already been
claimed.

Usage:
    uv run python .claude/tools/analyze_class_usage.py WikiPageDraft
    uv run python .claude/tools/analyze_class_usage.py WikiPageDraft --include-tests
    uv run python .claude/tools/analyze_class_usage.py WikiPageDraft --json > usages.json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

# ── data class ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClassUsage:
    file_path: str
    line_number: int
    usage_type: str  # import | inheritance | instantiation | type_annotation | reference | documentation
    line_content: str


# ── AST analysis ─────────────────────────────────────────────────────────────


class _UsageVisitor(ast.NodeVisitor):
    """Single-pass visitor: classifies every occurrence of `class_name`."""

    def __init__(self, class_name: str, file_path: str, lines: list[str]) -> None:
        self.class_name = class_name
        self.file_path = file_path
        self.lines = lines
        self.usages: list[ClassUsage] = []
        self._claimed: set[int] = set()  # id() of Name nodes claimed by specialized visitors
        self._annotation_node_ids: set[int] = set()  # id() of every node inside any annotation subtree

    # ── public entry point ───────────────────────────────────────────────────

    def run(self, tree: ast.AST) -> list[ClassUsage]:
        self._collect_annotation_nodes(tree)
        self.visit(tree)
        return self.usages

    # ── annotation pre-pass ──────────────────────────────────────────────────

    def _collect_annotation_nodes(self, tree: ast.AST) -> None:
        """Walk the tree once, recording the id() of every descendant of an
        annotation expression. Used later by `visit_Name` to classify Names
        inside `Optional[Foo]` / `list[Foo]` / etc. as type_annotation rather
        than plain reference.
        """
        for node in ast.walk(tree):
            annotations: list[ast.AST] = []
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.returns is not None:
                    annotations.append(node.returns)
                for arg in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs):
                    if arg.annotation is not None:
                        annotations.append(arg.annotation)
                if node.args.vararg and node.args.vararg.annotation:
                    annotations.append(node.args.vararg.annotation)
                if node.args.kwarg and node.args.kwarg.annotation:
                    annotations.append(node.args.kwarg.annotation)
            elif isinstance(node, ast.AnnAssign):
                annotations.append(node.annotation)
            elif isinstance(node, ast.arg) and node.annotation is not None:
                annotations.append(node.annotation)

            for ann in annotations:
                for descendant in ast.walk(ann):
                    self._annotation_node_ids.add(id(descendant))

    # ── specialized visitors (claim Names) ───────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == self.class_name or alias.name.split(".")[-1] == self.class_name:
                self._record(node.lineno, "import")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == self.class_name:
                self._record(node.lineno, "import")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == self.class_name:
                self._record(node.lineno, "inheritance")
                self._claimed.add(id(base))
            elif isinstance(base, ast.Attribute) and base.attr == self.class_name:
                self._record(node.lineno, "inheritance")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == self.class_name:
            self._record(node.lineno, "instantiation")
            self._claimed.add(id(node.func))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == self.class_name:
            self._record(node.lineno, "instantiation")
        self.generic_visit(node)

    # ── fallback visitor (only fires on un-claimed Names) ────────────────────

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != self.class_name:
            return
        if id(node) in self._claimed:
            return
        usage_type = "type_annotation" if id(node) in self._annotation_node_ids else "reference"
        self._record(node.lineno, usage_type)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _record(self, lineno: int, usage_type: str) -> None:
        line_content = self.lines[lineno - 1] if 0 < lineno <= len(self.lines) else ""
        self.usages.append(
            ClassUsage(
                file_path=self.file_path,
                line_number=lineno,
                usage_type=usage_type,
                line_content=line_content.rstrip(),
            )
        )


# ── orchestration ────────────────────────────────────────────────────────────


class ClassUsageAnalyzer:
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
        "site",
        "dist",
        "build",
    )

    def __init__(
        self,
        class_name: str,
        include_tests: bool = False,
        include_docs: bool = False,
        scan_dirs: list[str] | None = None,
    ) -> None:
        self.class_name = class_name
        self.include_tests = include_tests
        self.include_docs = include_docs
        if scan_dirs is None:
            scan_dirs = ["src"]
            if include_tests:
                scan_dirs.append("tests")
        self.scan_dirs = scan_dirs
        self.doc_dirs = ["docs"] if include_docs else []
        self.usages: list[ClassUsage] = []

    def analyze(self) -> dict[str, list[ClassUsage]]:
        for py in self._iter_python_files():
            self._analyze_python_file(py)
        for doc in self._iter_doc_files():
            self._analyze_doc_file(doc)

        grouped: dict[str, list[ClassUsage]] = defaultdict(list)
        seen: set[tuple[str, int, str]] = set()
        for u in self.usages:
            key = (u.file_path, u.line_number, u.usage_type)
            if key in seen:
                continue
            seen.add(key)
            grouped[u.usage_type].append(u)
        for key_list in grouped.values():
            key_list.sort(key=lambda u: (u.file_path, u.line_number))
        return dict(grouped)

    # ── file enumeration ─────────────────────────────────────────────────────

    def _iter_python_files(self) -> list[Path]:
        results: list[Path] = []
        for d in self.scan_dirs:
            base = Path(d)
            if not base.exists():
                print(f"⚠️  scan dir does not exist: {d}", file=sys.stderr)
                continue
            for py in base.rglob("*.py"):
                if any(part in self._EXCLUDED for part in py.parts):
                    continue
                results.append(py)
        return results

    def _iter_doc_files(self) -> list[Path]:
        results: list[Path] = []
        for d in self.doc_dirs:
            base = Path(d)
            if not base.exists():
                continue
            for ext in ("*.md", "*.rst", "*.txt"):
                for f in base.rglob(ext):
                    if any(part in self._EXCLUDED for part in f.parts):
                        continue
                    results.append(f)
        return results

    # ── per-file analysis ────────────────────────────────────────────────────

    def _analyze_python_file(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"⚠️  could not read {path}: {exc}", file=sys.stderr)
            return
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            print(f"⚠️  syntax error in {path}: {exc}", file=sys.stderr)
            return
        lines = content.splitlines()
        self.usages.extend(_UsageVisitor(self.class_name, str(path), lines).run(tree))

    def _analyze_doc_file(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"⚠️  could not read {path}: {exc}", file=sys.stderr)
            return
        for i, line in enumerate(content.splitlines(), start=1):
            if self.class_name in line:
                self.usages.append(
                    ClassUsage(
                        file_path=str(path),
                        line_number=i,
                        usage_type="documentation",
                        line_content=line.rstrip(),
                    )
                )


# ── reporting ────────────────────────────────────────────────────────────────


def print_report(class_name: str, grouped: dict[str, list[ClassUsage]]) -> None:
    print(f"\nCLASS USAGE ANALYSIS: {class_name}")
    print("=" * 80)

    total = sum(len(v) for v in grouped.values())
    affected_files = {u.file_path for usages in grouped.values() for u in usages}
    print(f"total usages:    {total}")
    print(f"files affected:  {len(affected_files)}")

    if total == 0:
        print(f"\n✅ no usages of '{class_name}' found")
        return

    ordering = ["import", "inheritance", "instantiation", "type_annotation", "reference", "documentation"]
    for usage_type in ordering:
        usages = grouped.get(usage_type)
        if not usages:
            continue
        print(f"\n[{usage_type}] ({len(usages)} occurrences)")
        print("-" * 60)
        for u in usages:
            print(f"  {u.file_path}:{u.line_number}")
            print(f"    {u.line_content.strip()}")


def to_json(class_name: str, grouped: dict[str, list[ClassUsage]]) -> str:
    payload = {
        "class_name": class_name,
        "total_usages": sum(len(v) for v in grouped.values()),
        "files_affected": sorted({u.file_path for usages in grouped.values() for u in usages}),
        "usages_by_type": {k: [asdict(u) for u in v] for k, v in grouped.items()},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ── entry point ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze class usage across the codebase for safe renaming")
    parser.add_argument("class_name", help="Name of the class to analyze")
    parser.add_argument("--include-tests", action="store_true", help="Include tests/ in the scan")
    parser.add_argument("--include-docs", action="store_true", help="Include docs/ (regex-only, no AST)")
    parser.add_argument(
        "--scan-dir",
        action="append",
        default=None,
        help="Directory to scan (repeatable). Default: src (+ tests if --include-tests)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable report")
    parser.add_argument("--output", "-o", help="Write the report to a file (otherwise stdout)")
    args = parser.parse_args()

    analyzer = ClassUsageAnalyzer(
        args.class_name,
        include_tests=args.include_tests,
        include_docs=args.include_docs,
        scan_dirs=args.scan_dir,
    )
    grouped = analyzer.analyze()

    output = to_json(args.class_name, grouped) if args.json else None

    if args.output:
        if output is None:
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                print_report(args.class_name, grouped)
            Path(args.output).write_text(buf.getvalue(), encoding="utf-8")
        else:
            Path(args.output).write_text(output, encoding="utf-8")
        print(f"report written to: {args.output}")
    else:
        if output is None:
            print_report(args.class_name, grouped)
        else:
            print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
