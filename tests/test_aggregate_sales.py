import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from aggregate_sales import DataError, FIELDS, aggregate, main


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "sales.csv"
        self.output = Path(self.temp.name) / "daily.csv"

    def write(self, rows, fields=FIELDS, encoding="utf-8"):
        with self.source.open("w", encoding=encoding, newline="") as file:
            writer = csv.writer(file)
            writer.writerow(fields)
            writer.writerows(rows)

    def test_duplicates_statuses_cancelled_missing_quantity_and_sorting(self):
        self.write([
            ["1", "2026-09-02", "B", "2", "10.15", "Delivered "],
            ["1", "2026-09-02", "B", "2", "10.15", "delivered"],
            ["2", "2026-09-01", "A", "", "", "canceled"],
            ["3", "2026-09-01", "A", "3", "0.10", "DELIVERED"],
            ["4", "2026-09-01", "A", "99", "100", "CANCELLED"],
            ["5", "2026-09-01", "B", "1", "5", "delivered"],
        ])
        rows, stats = aggregate(self.source)
        self.assertEqual(rows, [("A", "2026-09-01", 3, "0.30"),
                                ("B", "2026-09-01", 1, "5.00"),
                                ("B", "2026-09-02", 1, "20.30")])
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(stats["cancelled_without_quantity"], 1)

    def test_conflicting_order_id_is_rejected(self):
        for position, value in [(1, "2026-09-03"), (2, "B"), (3, "2"),
                                (4, "20"), (5, "cancelled")]:
            with self.subTest(position=position):
                row = ["1", "2026-09-02", "A", "1", "10", "delivered"]
                changed = row.copy()
                changed[position] = value
                self.write([row, changed])
                with self.assertRaisesRegex(DataError, "противоречивые"):
                    aggregate(self.source)

    def test_invalid_fields_are_rejected(self):
        cases = [(0, ""), (1, "2026-02-30"), (1, "2026-9-02"), (2, ""),
                 (3, ""), (3, "0"), (3, "-1"), (3, "1.5"), (4, ""),
                 (4, "NaN"), (4, "Infinity"), (4, "-10"), (4, "1.001"),
                 (5, "delivred")]
        for position, value in cases:
            with self.subTest(position=position, value=value):
                row = ["1", "2026-09-02", "A", "1", "10", "delivered"]
                row[position] = value
                self.write([row])
                with self.assertRaisesRegex(DataError, "строка 2"):
                    aggregate(self.source)

    def test_empty_file_and_wrong_headers_are_rejected(self):
        self.source.write_text("", encoding="utf-8")
        with self.assertRaises(DataError):
            aggregate(self.source)
        for fields in [FIELDS[:-1], (*FIELDS[:-1], "quantity"), (*FIELDS, "extra")]:
            self.write([], fields=fields)
            with self.assertRaises(DataError):
                aggregate(self.source)

    def test_incorrect_field_count_is_rejected(self):
        for row in [["1"], ["1", "2026-09-02", "A", "1", "10", "delivered", "extra"]]:
            self.write([row])
            with self.assertRaisesRegex(DataError, "число полей"):
                aggregate(self.source)

    def test_header_only_creates_header_only_output(self):
        self.write([])
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["--input", str(self.source), "--output", str(self.output)])
        self.assertEqual(code, 0)
        self.assertEqual(self.output.read_text(), "sku,order_date,orders,revenue\n")

    def test_bom_and_exact_decimal_arithmetic(self):
        self.write([["1", "2026-09-02", "A", "3", "0.10", "delivered"],
                    ["2", "2026-09-02", "A", "1", "0.20", "delivered"]], encoding="utf-8-sig")
        self.assertEqual(aggregate(self.source)[0], [("A", "2026-09-02", 2, "0.50")])

    def test_invalid_input_preserves_existing_output(self):
        self.write([["1", "2026-09-02", "A", "", "10", "delivered"]])
        self.output.write_text("previous result", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(["--input", str(self.source), "--output", str(self.output)])
        self.assertEqual(code, 1)
        self.assertEqual(self.output.read_text(), "previous result")

    def test_input_cannot_be_overwritten(self):
        self.write([])
        before = self.source.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main(["--input", str(self.source), "--output", str(self.source)])
        self.assertEqual(exc.exception.code, 2)
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
