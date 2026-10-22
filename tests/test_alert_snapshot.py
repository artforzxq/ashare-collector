"""提醒必须冻结发出那一刻的风险参数，不能跟着 features_daily 一起漂移。"""

import json
import tempfile
import unittest
from pathlib import Path

from collector import alerts, db

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AlertRiskSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _row(self, **overrides):
        row = {
            "code": "SH600000", "state": "up", "close": 100.0,
            "position_cap": 0.4, "stop_level": 94.75, "stop_pct": -5.25,
            "risk_reward": 3.2, "expectancy": 0.55, "cost_pct": 0.302,
            "cost_r": 0.0575, "win_rate": 0.51, "feature_version": "v1.5",
        }
        row.update(overrides)
        return row

    def test_snapshot_contains_everything_needed_to_judge_later(self):
        snap = alerts.risk_snapshot(self._row())
        for key in ("position_cap", "stop_level", "risk_reward", "expectancy",
                    "cost_pct", "cost_r", "win_rate", "feature_version"):
            self.assertIn(key, snap)
        self.assertEqual(snap["stop_level"], 94.75)

    def test_persisted_alert_keeps_snapshot_after_features_are_recomputed(self):
        snap = alerts.risk_snapshot(self._row())
        candidate = alerts.Candidate(
            code="SH600000", signal_type="STATE_TO_UP", level="P1", price=100.0,
            message="状态转多", state="up", payload={"risk": snap},
        )
        hint = alerts.if_then_text(candidate)
        self.assertIn("若收盘跌破 94.750", hint)
        candidate.payload["if_then"] = hint

        db.upsert_rows(self.conn, "alerts", [{
            "created_at": "2026-10-22 15:10:00", "trade_date": "2026-10-22",
            "code": candidate.code, "level": candidate.level,
            "signal_type": candidate.signal_type, "state": candidate.state,
            "price": candidate.price, "message": candidate.message,
            "payload_json": db.dump_json(candidate.payload),
            "risk_json": db.dump_json(candidate.payload.get("risk")),
            "feature_version": "v1.5",
        }], ["code", "trade_date", "signal_type", "level"])

        # 第二天按新参数重算，只影响 features_daily；提醒里的快照必须原样保留。
        db.upsert_rows(self.conn, "features_daily", [{
            "code": candidate.code, "trade_date": "2026-10-22",
            "position_cap": 0.1, "stop_level": 90.0,
        }], ["code", "trade_date"])

        stored = db.query_one(self.conn, "SELECT risk_json, payload_json FROM alerts")
        risk = json.loads(stored["risk_json"])
        payload = json.loads(stored["payload_json"])
        self.assertEqual(risk["stop_level"], 94.75)
        self.assertEqual(payload["risk"]["position_cap"], 0.4)
        self.assertIn("若收盘跌破", payload["if_then"])


if __name__ == "__main__":
    unittest.main()
