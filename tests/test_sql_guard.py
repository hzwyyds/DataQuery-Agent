from app.query.sql_guard import SQLGuard

SCHEMA = {
    "orders": {"order_id": "VARCHAR", "region": "VARCHAR", "amount": "DOUBLE"},
    "returns": {"order_id": "VARCHAR", "amount": "DOUBLE"},
}


def test_guard_qualifies_columns_and_caps_limit() -> None:
    result = SQLGuard().validate(
        "SELECT region, SUM(amount) AS total FROM orders GROUP BY region LIMIT 9999",
        SCHEMA,
    )

    assert result.allowed
    assert "LIMIT 501" in result.normalized_sql
    assert "orders.region" in result.normalized_sql


def test_guard_can_preserve_full_query_for_separate_preview_execution() -> None:
    result = SQLGuard().validate(
        "SELECT region, amount FROM orders ORDER BY amount DESC",
        SCHEMA,
        apply_limit=False,
    )
    assert result.allowed
    assert "LIMIT" not in result.normalized_sql


def test_guard_rejects_expression_limit_and_external_scan() -> None:
    expression = SQLGuard().validate("SELECT * FROM orders LIMIT 400 + 200", SCHEMA)
    external = SQLGuard().validate("SELECT * FROM read_csv_auto('secret.csv')", SCHEMA)

    assert not expression.allowed
    assert expression.reason == "LIMIT must be an integer literal"
    assert not external.allowed


def test_guard_rejects_unknown_table_and_ambiguous_column() -> None:
    unknown = SQLGuard().validate("SELECT * FROM payments", SCHEMA)
    ambiguous = SQLGuard().validate(
        "SELECT amount FROM orders JOIN returns USING (order_id)", SCHEMA
    )

    assert not unknown.allowed
    assert not ambiguous.allowed
    assert "columns cannot be resolved safely" in ambiguous.reason
