"""胜率校准：样本门槛、净胜率、写回时升版本。"""

import tempfile
import unittest
from pathlib import Path

from collector import calibration, db

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _alerts(self, count: int, wins: int):
        rows = []
        for index in range(count):
            rows.append({
                "created_at": "2026-09-01 15:00:00",
                "trade_date": f"2026-09-{index % 28 + 1:02d}",
                "code": f"SH600{index:03d}",
                "level": "P1",
                "signal_type": "STATE_TO_UP",
                "outcome_20d": 1.0 if index < wins else -1.0,
            })
        db.upsert_rows(self.conn, "alerts", rows, ["code", "trade_date", "signal_type", "level"])

    def test_enough_samples_gives_net_win_rate(self):
        self._alerts(40, 24)
        result = calibration.build(self.conn, {"risk": {"calibration_min_samples": 30}})
        self.assertEqual(result["done"], 40)
        self.assertAlmostEqual(result["net_win_rate"], 0.6, places=3)
        self.assertAlmostEqual(result["suggested"], 0.6, places=3)

    def test_thin_sample_refuses_to_suggest(self):
        self._alerts(10, 8)
        result = calibration.build(self.conn, {"risk": {"calibration_min_samples": 30}})
        self.assertIsNone(result["suggested"])
        self.assertIn("样本不足", calibration.report(result))

    def test_write_updates_only_win_rate_and_bumps_version(self):
        path = self.root / "config.yaml"
        path.write_text(
            "project:\n  feature_version: \"v1.5\"\n"
            "risk:\n  win_rate: 0.5\n  calibration_min_samples: 30\n",
            encoding="utf-8",
        )
        change = calibration.write_win_rate(path, 0.612)
        text = path.read_text(encoding="utf-8")
        self.assertIn("win_rate: 0.612", text)
        self.assertIn('feature_version: "v1.6"', text)
        self.assertEqual(change["version"], "v1.6")


if __name__ == "__main__":
    unittest.main()
