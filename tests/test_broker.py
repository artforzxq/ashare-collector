"""券商 CSV：字段归一、重复导入、FIFO、与账本数量核对。"""

import tempfile
import unittest
from pathlib import Path

from collector import broker, db, journal

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.csv = self.root / "trades.csv"
        self.csv.write_text(
            "成交日期,证券代码,证券名称,买卖标志,成交价格,成交数量,成交金额,佣金,印花税,过户费\n"
            "2026-01-02,600000,浦发银行,买入,10,100,1000,5,0,0.1\n"
            "2026-01-03,600000,浦发银行,买入,12,100,1200,5,0,0.12\n"
            "2026-01-05,600000,浦发银行,卖出,15,150,2250,5,1.125,0.15\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_import_and_fifo(self):
        result = broker.import_csv(self.conn, self.csv, broker="测试券商")
        self.assertEqual(result["rows"], 3)
        self.assertEqual(result["new"], 3)
        fifo = broker.fifo(self.conn)
        self.assertEqual(len(fifo["matches"]), 2)
        self.assertAlmostEqual(fifo["realized"], 636.06, places=2)
        self.assertEqual(len(fifo["open_lots"]), 1)
        self.assertAlmostEqual(fifo["open_lots"][0]["quantity"], 50.0)
        self.assertEqual(fifo["unmatched"], [])
        again = broker.import_csv(self.conn, self.csv, broker="测试券商")
        self.assertEqual(again["new"], 0)

    def test_journal_quantity_mismatch_is_reported(self):
        broker.import_csv(self.conn, self.csv, broker="测试券商")
        journal.open_trade(
            self.conn, {"journal": {"limits": {}}},
            code="600000", framework="short", opened_on="2026-01-02",
            entry_price=10.0, stop_price=9.0, shares=100.0, account_equity=100000.0,
            thesis="测试", invalidation="跌破 9",
        )
        diff = broker.reconcile_with_journal(self.conn)
        self.assertEqual(diff[0]["code"], "SH600000")
        self.assertAlmostEqual(diff[0]["broker"], 50.0)
        self.assertAlmostEqual(diff[0]["journal"], 100.0)


if __name__ == "__main__":
    unittest.main()
