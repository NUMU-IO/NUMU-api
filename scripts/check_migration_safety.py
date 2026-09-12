#!/usr/bin/env python3
"""Reject Alembic upgrades that cannot coexist with the running API."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

DESTRUCTIVE_SQL = re.compile(
    r"\b(?:DROP\s+(?:TABLE|COLUMN|TYPE|SCHEMA|CONSTRAINT)|TRUNCATE\b|"
    r"RENAME\s+(?:TO|COLUMN)|ALTER\s+COLUMN\b.*?\b(?:TYPE|SET\s+NOT\s+NULL)\b)",
    re.IGNORECASE | re.DOTALL,
)


def literal_sql(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        return literal_sql(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_sql(node.left), literal_sql(node.right)
        return left + right if left is not None and right is not None else None
    return None


class UpgradeVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[tuple[int, str]] = []

    def reject(self, node: ast.AST, reason: str) -> None:
        self.errors.append((node.lineno, reason))

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else ""

        if name in {"drop_table", "drop_column", "rename_table"}:
            self.reject(node, f"{name} breaks the currently running API")

        if name == "alter_column":
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            if "new_column_name" in keywords or "type_" in keywords:
                self.reject(
                    node,
                    "column rename/type changes require an expand-contract rollout",
                )
            nullable = keywords.get("nullable")
            if isinstance(nullable, ast.Constant) and nullable.value is False:
                self.reject(node, "SET NOT NULL can break writes from the running API")

        if name == "add_column" and node.args:
            column = node.args[-1]
            if isinstance(column, ast.Call):
                keywords = {keyword.arg: keyword.value for keyword in column.keywords}
                nullable = keywords.get("nullable")
                default = keywords.get("server_default")
                if (
                    isinstance(nullable, ast.Constant)
                    and nullable.value is False
                    and (
                        default is None
                        or isinstance(default, ast.Constant)
                        and default.value is None
                    )
                ):
                    self.reject(
                        node,
                        "a new NOT NULL column needs a server_default during rollout",
                    )

        if name == "execute" and node.args:
            sql = literal_sql(node.args[0])
            if sql is None:
                self.reject(
                    node, "dynamic migration SQL cannot be checked for compatibility"
                )
            elif DESTRUCTIVE_SQL.search(sql):
                self.reject(
                    node, "destructive SQL requires a later contract deployment"
                )

        self.generic_visit(node)


def check(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    errors: list[tuple[int, str]] = []
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "upgrade"
        ):
            visitor = UpgradeVisitor()
            for statement in node.body:
                visitor.visit(statement)
            errors.extend(visitor.errors)
    return errors


def self_test() -> None:
    safe = ast.parse(
        "def upgrade():\n op.add_column('t', sa.Column('x', sa.Text(), nullable=True))"
    )
    unsafe = ast.parse("def upgrade():\n op.drop_column('t', 'x')")
    safe_visitor, unsafe_visitor = UpgradeVisitor(), UpgradeVisitor()
    safe_visitor.visit(safe.body[0])
    unsafe_visitor.visit(unsafe.body[0])
    assert not safe_visitor.errors
    assert unsafe_visitor.errors
    print("migration safety self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    failed = False
    for path in args.paths:
        for line, reason in check(path):
            failed = True
            print(f"{path}:{line}: unsafe online migration: {reason}")
    if failed:
        print(
            "Split this into expand, deploy/backfill, then a later contract migration."
        )
        return 1
    print(f"checked {len(args.paths)} migration(s): safe for an online rollout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
