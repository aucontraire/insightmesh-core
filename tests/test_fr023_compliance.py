"""FR-023 compliance check: src/exports.py must not reinvent echomine adapters.

Per spec FR-023 (Compliance verification clause): `src/exports.py` MUST NOT
import the `json` module for adapter-style parsing or import from `echomine`'s
internal submodules. This test inspects the module's import statements via AST
to catch any future regression that reintroduces hand-rolled adapter logic.

A `json` import in `src/exports.py` is acceptable when used for adapter
*selection* (peeking at the JSON root structure to pick which echomine adapter
to use) but never for adapter-style schema parsing. This test confirms the
import patterns rather than usage semantics; the broader anti-hand-rolling
rule is enforced by FR-024's prohibited internal-submodule imports.
"""

from __future__ import annotations

import ast
from pathlib import Path

EXPORTS_PY = Path(__file__).parent.parent / "src" / "exports.py"


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    """Return list of (module, names) tuples for all import statements."""
    tree = ast.parse(path.read_text())
    out: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [n.name for n in node.names]
            out.append((module, names))
        elif isinstance(node, ast.Import):
            for n in node.names:
                out.append((n.name, []))
    return out


class TestFR023Compliance:
    def test_no_imports_from_echomine_internal_submodules(self) -> None:
        """FR-024: only top-level echomine.* symbols may be imported."""
        imports = _imports(EXPORTS_PY)
        for module, _names in imports:
            assert not module.startswith("echomine."), (
                f"FR-023/FR-024 violation: src/exports.py imports from internal "
                f"echomine submodule '{module}'. Only top-level `from echomine import ...` "
                f"is allowed."
            )

    def test_imports_only_listed_echomine_symbols(self) -> None:
        """FR-024 enumerates the public-API symbols InsightMesh consumes."""
        allowed = {
            "ClaudeAdapter",
            "OpenAIAdapter",
            "Conversation",
            "Message",
            "ConversationProvider",
            "EchomineError",
            "ParseError",
            "ValidationError",
            "SchemaVersionError",
        }
        imports = _imports(EXPORTS_PY)
        for module, names in imports:
            if module == "echomine":
                for name in names:
                    assert name in allowed, (
                        f"FR-024 violation: src/exports.py imports `{name}` from echomine, "
                        f"but FR-024 enumerates the allowed symbols as {sorted(allowed)}. "
                        f"If a new symbol is needed, update FR-024 in spec.md first."
                    )
