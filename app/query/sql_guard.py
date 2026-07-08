from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

DENIED_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_parquet",
    "glob",
    "httpfs",
    "sqlite_scan",
    "postgres_scan",
    "mysql_scan",
}


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    normalized_sql: str | None = None
    reason: str | None = None


class SQLGuard:
    def validate(
        self,
        sql: str,
        schema: dict[str, dict[str, str]],
        *,
        max_rows: int = 500,
    ) -> GuardResult:
        if not sql or not sql.strip():
            return GuardResult(False, reason="SQL is empty")
        if len(sql) > 20_000:
            return GuardResult(False, reason="SQL exceeds the 20,000 character limit")
        try:
            statements = parse(sql, read="duckdb")
        except ParseError as exc:
            return GuardResult(False, reason=f"SQL cannot be parsed: {exc}")
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            return GuardResult(False, reason="only one read-only SELECT query is allowed")
        root = statements[0]
        if any(
            root.find(kind)
            for kind in (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Create,
                exp.Drop,
                exp.Alter,
                exp.Command,
            )
        ):
            return GuardResult(False, reason="data or schema modification is not allowed")
        ctes = {cte.alias_or_name.casefold() for cte in root.find_all(exp.CTE)}
        allowed_tables = {name.casefold() for name in schema}
        for table in root.find_all(exp.Table):
            if table.db or table.catalog:
                return GuardResult(False, reason="schema-qualified tables are not allowed")
            name = table.name.casefold()
            if name not in allowed_tables and name not in ctes:
                return GuardResult(False, reason=f"table is not in this workspace: {table.name}")
        for function in root.find_all(exp.Func):
            if function.sql_name().casefold() in DENIED_FUNCTIONS:
                return GuardResult(False, reason="external data access functions are not allowed")
        limit = root.args.get("limit")
        if limit is not None:
            expression = limit.expression
            if not isinstance(expression, exp.Literal) or not expression.is_int:
                return GuardResult(False, reason="LIMIT must be an integer literal")
            requested = int(expression.this)
            if requested < 0:
                return GuardResult(False, reason="LIMIT cannot be negative")
            limit.set("expression", exp.Literal.number(min(requested, max_rows + 1)))
        else:
            root = root.limit(max_rows + 1)
        try:
            qualified = qualify(
                root,
                dialect="duckdb",
                schema=schema,
                validate_qualify_columns=True,
                identify=False,
                quote_identifiers=True,
            )
        except (OptimizeError, ParseError) as exc:
            return GuardResult(False, reason=f"columns cannot be resolved safely: {exc}")
        return GuardResult(True, normalized_sql=qualified.sql(dialect="duckdb"))
