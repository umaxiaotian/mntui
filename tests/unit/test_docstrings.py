"""公開APIの日本語ドキュメントを機械的に検証する。"""

import ast
import re
from pathlib import Path


def test_public_api_docstrings() -> None:
    for path in Path("src/mntui").rglob("*.py"):
        tree = ast.parse(path.read_text())
        assert ast.get_docstring(tree), path
        public_nodes = list(tree.body)
        for parent in tree.body:
            if isinstance(parent, ast.ClassDef) and not parent.name.startswith("_"):
                public_nodes.extend(parent.body)
        for node in public_nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_") and node.name != "__init__":
                    continue
                if node.col_offset > 4:
                    continue
                doc = ast.get_docstring(node)
                assert doc and re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", doc), (path, node.name)
                assert ":param" not in doc and "Parameters\n---" not in doc
