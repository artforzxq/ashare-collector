"""关键位置上的形态提醒：只在关键位置响，级别只按证据强度分。

这是形态层第一次真正"被用起来"（以前 candle 只喂给风险层，提醒层完全没看它）。
两条底线：
  · **不做买卖判断**：消息只说"这里出现了什么"，所以它不进 BUY_SIGNALS，
    不参与方向性的风险否决与状态优先仲裁；
  · **默认沉默**：不在关键位置、或者形态不够硬，一条都不发。一年 250 根 K 线里
    六成以上都能叫出某个形态名，不加这两道过滤，提醒第一周就会被忽略。
"""

import unittest
import tempfile
from pathlib import Path

from collector import alerts, candles, db
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG = {"alerts": {"key_pattern": True}, "validation": {"volume_anomaly_ratio": 10.0}}


def _row(candle: dict, **overrides) -> dict:
    row = {"code": "SH600487", "close": 10.0, "state": "range", "opportunity_score": 0.5,
           "raw_values": {}, "candle": candle}
    row.update(overrides)
    return row


def _candle(**overrides) -> dict:
    base = candles.blank()
    base.update({"pattern": "长阳", "key": True, "key_reasons": ["20 日区间下沿"], "volume_x": 1.8})
    base.update(overrides)
    return base


class KeyPatternAlertTests(unittest.TestCase):
    def _signals(self, row, cfg=None):
        return [c for c in alerts.build_candidates(row, None, [], cfg or CONFIG)
                if c.signal_type.startswith("KEY_PATTERN")]

    def test_reversal_at_a_key_position_is_p1(self):
        row = _row(_candle(pattern="放量长阳、阳包阴", long_bull=True, bullish_engulf=True,
                           reversal_up=True))
        found = self._signals(row)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].signal_type, "KEY_PATTERN_STRONG")
        self.assertEqual(found[0].level, "P1")
        self.assertIn("反转向上确认", found[0].message)
        self.assertIn("20 日区间下沿", found[0].message)

    def test_single_pattern_at_a_key_position_is_p2(self):
        row = _row(_candle(pattern="放量长阳", long_bull=True))
        found = self._signals(row)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].signal_type, "KEY_PATTERN")
        self.assertEqual(found[0].level, "P2")
        self.assertIn("量 1.80 倍", found[0].message)

    def test_combo_only_counts_as_a_reversal(self):
        """组合形态本身够硬（它已经把两三根 K 线的关系算进去了），不必再要放量。"""
        row = _row(_candle(pattern="早晨之星", combo_up=True, reversal_up=True, volume_x=1.0))
        found = self._signals(row)
        self.assertEqual(found[0].signal_type, "KEY_PATTERN")
        self.assertTrue(found[0].payload["combo"])

    def test_a_reversal_in_the_same_direction_trend_is_downgraded_not_dropped(self):
        """上升趋势里的"放量长阳 + 站上前 10 日高点"是延续，不是反转：不该按 P1 喊人，
        但它确实是关键位上的放量突破，够 P2 记一笔。实测不加这条状态前提，
        8 只标的 120 个交易日要响 221 次 P1。"""
        row = _row(_candle(pattern="放量长阳、站上前 10 日高点", long_bull=True,
                           reversal_up=True), state="up")
        found = self._signals(row)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].signal_type, "KEY_PATTERN")
        self.assertFalse(found[0].payload["reversal"])

    def test_quiet_single_pattern_does_not_alert(self):
        """没有量的单根形态只画在图上：实测 79% 的候选是平量，全发出去就是噪音。"""
        row = _row(_candle(pattern="长阳、站上前 10 日高点", long_bull=True,
                           reversal_up=True, volume_x=1.0))
        self.assertEqual(self._signals(row), [])

    def test_the_volume_threshold_comes_from_the_candle_layer(self):
        """放量门槛只有一份：形态层认定"明显"的，提醒层才可能响。"""
        threshold = candles.THRESHOLDS["volume_confirm_x"]
        row = _row(_candle(pattern="长阳", long_bull=True, volume_x=threshold - 0.1))
        self.assertEqual(self._signals(row), [])

    def test_no_alert_away_from_key_positions(self):
        row = _row(_candle(key=False, key_reasons=[]))
        self.assertEqual(self._signals(row), [])

    def test_no_alert_for_a_weak_pattern(self):
        """十字星这种"描述"不是形态——`obvious` 不过关就不发。"""
        row = _row(_candle(pattern="十字星", doji=True, volume_x=1.0))
        self.assertEqual(self._signals(row), [])

    def test_no_alert_without_a_candle_at_all(self):
        row = _row(_candle())
        row.pop("candle")
        self.assertEqual(self._signals(row), [])

    def test_switch_off_silences_it(self):
        row = _row(_candle(reversal_up=True))
        cfg = {"alerts": {"key_pattern": False}, "validation": {"volume_anomaly_ratio": 10.0}}
        self.assertEqual(self._signals(row, cfg), [])

    def test_it_is_not_a_buy_signal(self):
        """形态提醒是"这里出现了什么"的事实描述，不该被当成买入信号去仲裁。"""
        self.assertNotIn("KEY_PATTERN", alerts.BUY_SIGNALS)
        self.assertNotIn("KEY_PATTERN_STRONG", alerts.BUY_SIGNALS)

    def test_it_survives_the_whole_arbitration_chain(self):
        """走一遍完整的候选生成（含其它规则），确认它不会因为缺字段而崩。"""
        row = _row(_candle(reversal_up=True))
        candidates = alerts.build_candidates(row, None, [], CONFIG)
        self.assertTrue(any(c.signal_type == "KEY_PATTERN_STRONG" for c in candidates))


class KeyPatternCooldownTests(unittest.TestCase):
    """按信号覆盖冷却期：一段突破行情会连着好几天长一个样，不能连响四天。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.cfg = {"alerts": {"cooldown_minutes_p0": 15, "cooldown_days_p1": 1,
                               "cooldown_days_p2": 5,
                               "cooldown_days_by_signal": {"KEY_PATTERN_STRONG": 5, "KEY_PATTERN": 10}}}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _persist(self, day: str, level: str, signal: str):
        candidate = alerts.Candidate("SH600606", signal, level, 1.45, "关键位形态", "range", 0.5)
        alerts.persist(self.conn, [candidate], [], day, "v1")

    def _blocked(self, day: str, level: str, signal: str) -> bool:
        candidate = alerts.Candidate("SH600606", signal, level, 1.45, "关键位形态", "range", 0.5)
        kept, suppressed = alerts.apply_cooldown(self.conn, [candidate], self.cfg, day)
        return not kept

    def test_the_override_beats_the_level_default(self):
        self._persist("2026-09-14", "P1", "KEY_PATTERN_STRONG")
        # 级别默认只隔 1 天，但关键位形态覆盖成 5 天
        self.assertTrue(self._blocked("2026-09-16", "P1", "KEY_PATTERN_STRONG"))

    def test_a_different_signal_is_unaffected(self):
        self._persist("2026-09-14", "P1", "STATE_TO_UP")
        self.assertFalse(self._blocked("2026-09-16", "P1", "KEY_PATTERN_STRONG"))

    def test_after_the_window_it_fires_again(self):
        self._persist("2026-09-14", "P1", "KEY_PATTERN_STRONG")
        self.assertFalse(self._blocked("2026-09-21", "P1", "KEY_PATTERN_STRONG"))


class KeyPatternPipelineTests(unittest.TestCase):
    """端到端：K 线落库 → 特征 → 风险层 → 提醒，确认提醒真的会落进 alerts 表。

    单元测试只能证明"规则算得对"；这一条证明"它接上了"——形态是在特征层算的，
    风险层和提醒层读的是同一份结论，最后写进 alerts 表。
    """

    CODE = "SH600487"
    DATE = "2026-09-18"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(self.tmp.name))
        self.cfg["_db_path"] = str(Path(self.tmp.name) / "t.db")
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": [self.CODE]}
        # 仲裁层的 R3「默认沉默」会把机会分落在中性带内的候选全部拦下——这条规则
        # 有自己的测试，这里要验的是"形态有没有接上"，所以把带子收成 0。
        self.cfg["state"]["neutral_band"] = 0.0
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(self.conn, "bars_daily", self._bars(), ["code", "trade_date"])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _bars(self) -> list[dict]:
        """下跌 → 横盘 20 天 → 最后一根放量长阳突破区间上沿（关键位置 + 反转 + 放量）。"""
        rows = []
        price = 12.0
        for index in range(100):                      # 缓慢下跌，把趋势做出来
            price *= 0.995
            rows.append((price * 0.998, price * 1.004, price * 0.996, price, 1_000_000.0))
        for index in range(30):                       # 横盘，形成 20 日区间
            wobble = 1 + (index % 3 - 1) * 0.004
            rows.append((price * 0.999, price * 1.002, price * 0.998, price * wobble, 1_000_000.0))
        rows.append((10.5, 11.6, 10.45, 11.5, 3_000_000.0))     # 放量长阳，收盘破 20 日高
        return [
            {
                "code": self.CODE, "trade_date": f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}",
                "open": row[0], "high": row[1], "low": row[2], "close": row[3],
                "volume": row[4], "amount": row[3] * row[4], "pct_chg": 0.0, "quality_flag": "ok",
            }
            for index, row in enumerate(rows)
        ]

    def test_the_alert_lands_in_the_alerts_table(self):
        from collector import tasks
        from collector.registry import FactorRegistry

        registry = FactorRegistry(self.cfg.get("factors", []), "v1")
        # 直接用最后一天那根 K 线的日期作为"交易日"
        day = db.query_one(self.conn, "SELECT MAX(trade_date) AS d FROM bars_daily")["d"]
        state_rows = tasks._compute_features(self.conn, self.cfg, registry, day, verbose=False)
        bands = tasks._compute_levels(self.conn, self.cfg, day, verbose=False)
        tasks._apply_risk(self.conn, self.cfg, day, state_rows, bands, verbose=False)
        tasks._decide(self.conn, self.cfg, day, state_rows, bands, "ok", {"issues": []}, verbose=False)

        row = db.query_one(
            self.conn,
            "SELECT signal_type, level, message FROM alerts WHERE signal_type LIKE 'KEY_PATTERN%'",
        )
        self.assertIsNotNone(row, "关键位形态没有生成提醒")
        self.assertEqual(row["signal_type"], "KEY_PATTERN_STRONG")
        self.assertIn("反转向上确认", row["message"])
        self.assertIn("关键位置", row["message"])
        # 形态也要落到 features_daily 的**当日那一行**（页面和 AI 读的是这一列）
        feature = db.query_one(
            self.conn,
            "SELECT candle_pattern FROM features_daily WHERE code=? AND trade_date=?",
            (self.CODE, day),
        )
        self.assertTrue(feature["candle_pattern"], feature)


if __name__ == "__main__":
    unittest.main()
