"""证据检验：前瞻收益口径 + 小样本警告。"""

import tempfile
import unittest
from pathlib import Path

from collector import db, evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_market_forward_enters_next_day(self):
        rows = [
            {"code": "SH600000", "trade_date": f"2026-01-{index + 1:02d}",
             "close": 100.0 * (1.01 ** index), "pct_chg": 1.0}
            for index in range(30)
        ]
        db.upsert_rows(self.conn, "bars_daily", rows, ["code", "trade_date"])
        frame = evidence._market_forward(self.conn, horizon=5, step=1)
        # 次日建仓、持有 5 日：1.01^6 / 1.01^1 − 1 = 1.01^5 − 1
        self.assertAlmostEqual(float(frame["fwd"].iloc[0]), (1.01 ** 5 - 1) * 100, places=3)

    def test_report_warns_when_gate4_sample_is_thin(self):
        sent = {
            "ok": True, "horizon": 20, "step": 5, "baseline": 1.0, "sample_days": 20,
            "rows": [{"stage": "发酵", "days": 4, "mean": 2.0, "excess": 1.0,
                      "win_rate": 50.0, "t": 1.0, "p_adj": 0.5}],
        }
        line = {
            "ok": True, "horizon": 20, "step": 5, "caveat": "近似口径",
            "rows": [{"gates": 4, "days": 6, "samples": 6, "excess": 9.0,
                      "win_rate": 83.0, "t": 2.0, "p": 0.04}],
            "paired": {"days": 6, "diff": 9.0, "t": 2.0, "p": 0.04},
        }
        text = evidence.report(sent, line)
        self.assertIn("不足 10 个采样日", text)
        self.assertIn("配对样本不足 10 个交易日", text)
        self.assertIn("不足以证明它是稳定规律", text)


if __name__ == "__main__":
    unittest.main()
