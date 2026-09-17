"""审计与提醒的卫生问题：重跑不许重复记账、数据恢复后要撤掉异常提醒。"""

import tempfile
import unittest
from pathlib import Path

from collector import db, tasks
from collector.alerts import Candidate, persist
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-09-16"


class ArbitrationIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.candidate = Candidate("SH000300", "STATE_TO_UP", "P1", 4480.0, "状态转上升趋势", "up", 0.7)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _count(self) -> int:
        return db.query_one(self.conn, "SELECT COUNT(*) AS n FROM arbitration_log")["n"]

    def test_same_suppression_written_twice_keeps_one_row(self):
        logs = [{"candidate": self.candidate, "rule": "R3_NEUTRAL_SILENCE", "reason": "不确定", "party_b": "机会层"}]
        persist(self.conn, [], logs, DATE, "v1")
        persist(self.conn, [], logs, DATE, "v1")      # 同一天重跑
        self.assertEqual(self._count(), 1)

    def test_different_rules_are_kept_separately(self):
        persist(self.conn, [], [{"candidate": self.candidate, "rule": "R3_NEUTRAL_SILENCE", "reason": "a"}], DATE, "v1")
        persist(self.conn, [], [{"candidate": self.candidate, "rule": "R6_BUDGET", "reason": "b"}], DATE, "v1")
        self.assertEqual(self._count(), 2)

    def test_rerun_does_not_wipe_backfilled_outcome(self):
        logs = [{"candidate": self.candidate, "rule": "R3_NEUTRAL_SILENCE", "reason": "不确定"}]
        persist(self.conn, [], logs, DATE, "v1")
        self.conn.execute("UPDATE arbitration_log SET outcome_20d=3.5")
        self.conn.commit()
        persist(self.conn, [], logs, DATE, "v1")
        row = db.query_one(self.conn, "SELECT outcome_20d FROM arbitration_log")
        self.assertEqual(row["outcome_20d"], 3.5)     # 回填结果不能被重跑覆盖掉


class StaleAnomalyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cfg_path = self.root / "config.yaml"
        cfg_path.write_text((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        self.cfg = load_config(cfg_path, project_root=PROJECT_ROOT)
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(
            self.conn,
            "alerts",
            [{
                "created_at": f"{DATE} 20:00:00", "trade_date": DATE, "code": "SH000300",
                "level": "P0", "signal_type": "DATA_ANOMALY",
                "message": "数据质量异常（stale），已冻结自动提醒",
            }],
            ["code", "trade_date", "signal_type", "level"],
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _anomalies(self) -> int:
        return db.query_one(
            self.conn, "SELECT COUNT(*) AS n FROM alerts WHERE signal_type='DATA_ANOMALY'"
        )["n"]

    def _decide(self, quality_flag: str, missing_grade: str = "ok"):
        rows = {
            "SH000300": {
                "code": "SH000300", "trade_date": DATE, "close": 4480.0, "state": "range",
                "state_days": 5, "opportunity_score": 0.5, "quality_flag": quality_flag,
                "raw_values": {}, "range_position": 0.5,
            }
        }
        return tasks._decide(self.conn, self.cfg, DATE, rows, {}, missing_grade, {"issues": []}, False)

    def test_healthy_data_removes_leftover_anomaly_alert(self):
        self.assertEqual(self._anomalies(), 1)
        self._decide("ok")
        self.assertEqual(self._anomalies(), 0)

    def test_bad_data_keeps_the_anomaly_alert(self):
        self._decide("suspect")
        self.assertEqual(self._anomalies(), 1)

    def test_critical_missing_keeps_the_anomaly_alert(self):
        self._decide("ok", missing_grade="L3")     # 关键标的缺失时仍然冻结
        self.assertEqual(self._anomalies(), 1)


if __name__ == "__main__":
    unittest.main()
