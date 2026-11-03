"""Independent check for the supplied fixture, using SQLite instead of the main script.

This verifier intentionally supports the fixture's integer prices, not arbitrary new inputs.
"""

import csv
import re
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path


def main():
    with Path("sales.csv").open(encoding="utf-8", newline="") as file:
        raw = list(csv.DictReader(file))
    unique = {tuple(row.values()) for row in raw}
    ids = {row[0] for row in unique}
    if len(unique) != len(ids):
        raise ValueError("В исходных данных есть конфликтующие order_id")
    # Validate SQLite casts before using them: never turn malformed values into zero.
    for row in raw:
        if row["status"].strip().lower() == "delivered":
            if not re.fullmatch(r"[1-9][0-9]*", row["quantity"]):
                raise ValueError("Проверка рассчитана на целое положительное количество")
            if not re.fullmatch(r"[0-9]+", row["price"]):
                raise ValueError("Проверка рассчитана на целые цены в данной выгрузке")
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE sales (order_id TEXT, order_date TEXT, sku TEXT, quantity TEXT, price TEXT, status TEXT)")
        db.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?)", [tuple(r.values()) for r in raw])
        expected = db.execute("""
            WITH deduplicated AS (SELECT DISTINCT * FROM sales)
            SELECT sku, order_date, COUNT(*),
                   SUM(CASE WHEN lower(trim(status)) = 'delivered'
                            THEN CAST(quantity AS INTEGER) * CAST(price AS INTEGER)
                            ELSE 0 END)
            FROM deduplicated GROUP BY sku, order_date ORDER BY sku, order_date
        """).fetchall()
    with Path("daily_by_sku.csv").open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != ["sku", "order_date", "orders", "revenue"]:
            raise ValueError("Неверные колонки результата")
        actual = [(r["sku"], r["order_date"], int(r["orders"]), Decimal(r["revenue"])) for r in reader]
    if actual != expected:
        raise ValueError("Результат не совпадает с независимым SQL-расчётом")
    statuses = Counter(row[5].strip().lower().replace("canceled", "cancelled") for row in unique)
    orders = sum(row[2] for row in expected)
    revenue = sum(row[3] for row in expected)
    # Fixed regression controls for this exact input; SQL also compares all output rows.
    if (len(raw), len(unique), len(expected), orders, revenue) != (13858, 13798, 631, 13798, 10276540):
        raise ValueError(f"Неожиданные контрольные цифры: {len(raw)}, {len(unique)}, {len(expected)}, {orders}, {revenue}")
    print(f"OK: все {len(expected)} строк совпали с SQLite, включая порядок и выручку.")
    print(f"Строк исходника: {len(raw)}; уникальных заказов: {orders}; дубликатов: {len(raw)-len(unique)}")
    print(f"Статусы после дедупликации: {dict(sorted(statuses.items()))}")
    print(f"Выручка: {revenue} руб.")
    print("Первая группа:", expected[0])
    print("Последняя группа:", expected[-1])


if __name__ == "__main__":
    main()
