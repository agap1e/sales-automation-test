"""Aggregate daily orders and delivered revenue using only the standard library."""

import argparse
import csv
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


FIELDS = ("order_id", "order_date", "sku", "quantity", "price", "status")
OUTPUT_FIELDS = ("sku", "order_date", "orders", "revenue")


class DataError(ValueError):
    """Input cannot be interpreted without guessing."""


def parse_row(raw):
    """Normalize known status variants; require monetary inputs only for delivery."""
    row = {key: raw[key].strip() for key in FIELDS}
    for key in ("order_id", "order_date", "sku"):
        if not row[key]:
            raise DataError(f"пустое поле {key}")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", row["order_date"]):
        raise DataError("ожидается дата ГГГГ-ММ-ДД")
    try:
        date.fromisoformat(row["order_date"])
    except ValueError as exc:
        raise DataError("несуществующая дата") from exc
    status = row["status"].lower()
    if status == "canceled":
        status = "cancelled"
    if status not in ("delivered", "cancelled"):
        raise DataError(f"неизвестный статус {row['status']!r}")
    row["status"] = status
    if status == "delivered":
        if not re.fullmatch(r"[0-9]+", row["quantity"]):
            raise DataError("quantity для delivered должно быть целым положительным числом")
        quantity = int(row["quantity"])
        if quantity <= 0:
            raise DataError("quantity для delivered должно быть больше нуля")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", row["price"]):
            raise DataError("price для delivered должно быть неотрицательным числом с точностью до копеек")
        try:
            price = Decimal(row["price"])
        except InvalidOperation as exc:
            raise DataError("некорректная цена") from exc
        # Convert to integer kopecks before arithmetic to avoid rounding errors.
        rubles, _, kopecks = format(price, "f").partition(".")
        unit_kopecks = int(rubles) * 100 + int((kopecks + "00")[:2])
        row["quantity"] = str(quantity)
        row["price"] = f"{unit_kopecks // 100}.{unit_kopecks % 100:02d}"
        revenue = quantity * unit_kopecks
    else:
        # Cancelled orders contribute one order and zero revenue even if amounts are absent.
        revenue = 0
    return row, revenue


def aggregate(input_path):
    seen = {}
    totals = {}
    stats = Counter()
    with Path(input_path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames is None or len(reader.fieldnames) != len(FIELDS) or set(reader.fieldnames) != set(FIELDS):
            raise DataError(f"ожидаются ровно колонки: {', '.join(FIELDS)}")
        try:
            for raw in reader:
                stats["input_rows"] += 1
                if None in raw or any(value is None for value in raw.values()):
                    raise DataError(f"строка {reader.line_num}: неверное число полей")
                try:
                    row, revenue = parse_row(raw)
                except DataError as exc:
                    raise DataError(f"строка {reader.line_num}: {exc}") from exc
                if raw["status"] != row["status"]:
                    stats["normalized_statuses"] += 1
                order_id = row["order_id"]
                if order_id in seen:
                    if seen[order_id] != row:
                        raise DataError(f"строка {reader.line_num}: противоречивые данные заказа {order_id}")
                    stats["duplicates"] += 1
                    continue
                seen[order_id] = row
                stats[row["status"]] += 1
                if row["status"] == "cancelled" and not row["quantity"]:
                    stats["cancelled_without_quantity"] += 1
                key = (row["sku"], row["order_date"])
                if key not in totals:
                    totals[key] = [0, 0]
                totals[key][0] += 1
                totals[key][1] += revenue
        except csv.Error as exc:
            raise DataError(f"CSV около строки {reader.line_num}: {exc}") from exc
    result = []
    for (sku, day), (orders, revenue) in sorted(totals.items()):
        result.append((sku, day, orders, f"{revenue // 100}.{revenue % 100:02d}"))
    stats["groups"] = len(result)
    stats["revenue_kopecks"] = sum(item[1] for item in totals.values())
    return result, stats


def write_result(output_path, rows):
    """Replace the output only after a complete successful write."""
    output_path = Path(output_path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=output_path.parent,
                                         prefix=".daily-", suffix=".csv", delete=False) as output:
            temporary_path = Path(output.name)
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(OUTPUT_FIELDS)
            writer.writerows(rows)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Заказы и выручка по артикулу и дню")
    parser.add_argument("--input", type=Path, default=Path("sales.csv"))
    parser.add_argument("--output", type=Path, default=Path("daily_by_sku.csv"))
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error("входной и выходной файлы должны различаться")
    try:
        rows, stats = aggregate(args.input)
        write_result(args.output, rows)
    except (DataError, OSError, UnicodeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    total = stats["revenue_kopecks"]
    print(f"Строк: {stats['input_rows']}; дубликатов: {stats['duplicates']}; "
          f"delivered: {stats['delivered']}; cancelled: {stats['cancelled']}; "
          f"исправлено статусов: {stats['normalized_statuses']}; "
          f"отмен без количества: {stats['cancelled_without_quantity']}; "
          f"групп: {stats['groups']}; выручка: {total // 100}.{total % 100:02d} руб.")
    print(f"Результат: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
