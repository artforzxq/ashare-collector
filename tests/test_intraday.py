"""当日分时线：采集、落库、清理、页面 API（全部离线，走夹具源）。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import db, intraday, server, tasks
from collector.config import load_config, use_fixture_sources
from collector.sources.tencent_source import _parse_intraday

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TencentParseTests(unittest.TestCase):
    """腾讯分时的解析。接口字段位置会变，所以用固定样本钉住。"""

    def test_parses_time_price_and_volume(self):
        # 真实格式：时间 价格 累计成交量(手) 累计成交额(元)
        payload = {"data": {"sh600519": {"data": {"date": "20260917", "data": [
            "0930 1500.00 120 180000000.00",
            "0931 1505.00 200 301000000.00",
        ]}}}}
        rows = _parse_intraday(payload, "sh600519", "SH600519")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["dt"], "2026-09-17 09:30")
        self.assertEqual(rows[0]["close"], 1500.00)
        self.assertEqual(rows[0]["volume"], 12000.0)          # 手 → 股
        self.assertEqual(rows[0]["amount"], 180000000.0)      # 成交额是真值，不是估算
        self.assertEqual(rows[0]["period"], 1)

    def test_cumulative_volume_and_amount_become_per_minute(self):
        """接口给的是当天累计值，必须差分——之前把累计当每分钟，量柱才会一路往上涨。"""
        payload = {"data": {"sh600519": {"data": {"date": "20260917", "data": [
            "0930 100.00 10 100000.00",
            "0931 101.00 25 252500.00",
            "0932 102.00 25 252500.00",          # 这一分钟没成交
        ]}}}}
        rows = _parse_intraday(payload, "sh600519", "SH600519")
        self.assertEqual([row["volume"] for row in rows], [1000.0, 1500.0, 0.0])
        self.assertEqual([row["amount"] for row in rows], [100000.0, 152500.0, 0.0])

    def test_bad_lines_are_skipped_not_fatal(self):
        payload = {"data": {"sh600519": {"data": {"date": "20260917", "data": [
            "", "0930", "0931 -- 10", "0932 1505.00 80",
        ]}}}}
        rows = _parse_intraday(payload, "sh600519", "SH600519")
        self.assertEqual([row["dt"][-5:] for row in rows], ["09:32"])

    def test_empty_payload_returns_nothing(self):
        self.assertEqual(_parse_intraday({}, "sh600519", "SH600519"), [])


def _full_session() -> list[str]:
    """240 个可交易分钟：09:30–11:29、13:00–14:59。"""
    out: list[str] = []
    for start, end in ((570, 689), (780, 899)):
        out += [f"{m // 60:02d}:{m % 60:02d}" for m in range(start, end + 1)]
    return out


class AggregateTests(unittest.TestCase):
    """1 分钟 → 5/30/60 分钟。口径和行情软件一致：一天 240 个可交易分钟。"""

    def test_session_index_folds_the_closing_prints(self):
        self.assertEqual(intraday.session_index("2026-09-17 09:30"), 0)
        self.assertEqual(intraday.session_index("2026-09-17 09:31"), 1)
        self.assertEqual(intraday.session_index("2026-09-17 11:29"), 119)
        self.assertEqual(intraday.session_index("2026-09-17 11:30"), 119)   # 并进上午最后一根
        self.assertEqual(intraday.session_index("2026-09-17 13:00"), 120)
        self.assertEqual(intraday.session_index("2026-09-17 15:00"), 239)   # 并进下午最后一根
        self.assertIsNone(intraday.session_index("2026-09-17 12:00"))       # 午休
        self.assertIsNone(intraday.session_index("不是时间"))

    def _points(self):
        return [
            {"code": "SH600000", "dt": f"2026-09-17 {clock}", "close": 10.0,
             "volume": 100.0, "amount": 1000.0}
            for clock in _full_session()
        ]

    def test_a_full_day_gives_the_expected_bar_counts(self):
        points = self._points()
        bars30 = intraday.aggregate_points(points, 30)
        self.assertEqual(len(bars30), 8)                    # 和行情软件一样是 8 根
        self.assertEqual(bars30[0]["dt"], "2026-09-17 09:30")
        self.assertEqual(bars30[4]["dt"], "2026-09-17 13:00")   # 下午第一根
        self.assertEqual(len(intraday.aggregate_points(points, 5)), 48)
        self.assertEqual(len(intraday.aggregate_points(points, 60)), 4)
        self.assertEqual(sum(bar["volume"] for bar in bars30), 240 * 100.0)

    def test_ohlc_and_amount_are_rolled_up(self):
        points = [
            {"code": "SH600000", "dt": "2026-09-17 09:30", "close": 10.0, "volume": 100.0, "amount": 1000.0},
            {"code": "SH600000", "dt": "2026-09-17 09:31", "close": 10.5, "volume": 200.0, "amount": 2100.0},
            {"code": "SH600000", "dt": "2026-09-17 09:32", "close": 9.8, "volume": 300.0, "amount": 2940.0},
            {"code": "SH600000", "dt": "2026-09-17 09:33", "close": 10.2, "volume": 400.0, "amount": 4080.0},
        ]
        bar = intraday.aggregate_points(points, 5)[0]
        self.assertEqual((bar["open"], bar["high"], bar["low"], bar["close"]), (10.0, 10.5, 9.8, 10.2))
        self.assertEqual(bar["volume"], 1000.0)
        self.assertEqual(bar["amount"], 10120.0)
        self.assertEqual(bar["period"], 5)

    def test_a_half_interval_still_produces_one_bar(self):
        # 盘中就该这样：先给半根，收盘后自然补齐
        points = self._points()[:7]
        bars = intraday.aggregate_points(points, 30)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["dt"], "2026-09-17 09:30")

    def test_points_outside_the_session_are_ignored(self):
        points = [
            {"code": "SH600000", "dt": "2026-09-17 09:25", "close": 10.0, "volume": 1.0, "amount": 10.0},
            {"code": "SH600000", "dt": "2026-09-17 09:30", "close": 11.0, "volume": 2.0, "amount": 22.0},
        ]
        bars = intraday.aggregate_points(points, 30)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["open"], 11.0)              # 集合竞价那笔不算

    def test_period_one_is_rejected(self):
        with self.assertRaises(ValueError):
            intraday.aggregate_points(self._points(), 1)


class IntradayCollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        use_fixture_sources(self.cfg)                  # 离线：夹具源也给分时
        self.cfg["_db_path"] = str(root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_collect_writes_a_full_session(self):
        info = tasks.collect_intraday_bars(self.conn, self.cfg, ["SH000300"], verbose=False)
        self.assertTrue(info["ok"])
        row = db.query_one(
            self.conn, "SELECT COUNT(*) n, MIN(dt) first_dt, MAX(dt) last_dt FROM bars_intraday")
        self.assertGreater(row["n"], 200)             # 一个交易日 240 个点上下
        self.assertTrue(row["first_dt"].endswith("09:30"))
        self.assertTrue(row["last_dt"].endswith("15:00"))

    def test_collect_is_idempotent(self):
        tasks.collect_intraday_bars(self.conn, self.cfg, ["SH000300"], verbose=False)
        first = db.query_one(self.conn, "SELECT COUNT(*) n FROM bars_intraday")["n"]
        tasks.collect_intraday_bars(self.conn, self.cfg, ["SH000300"], verbose=False)
        self.assertEqual(db.query_one(self.conn, "SELECT COUNT(*) n FROM bars_intraday")["n"], first)

    def test_collect_also_writes_the_aggregated_periods(self):
        info = tasks.collect_intraday_bars(self.conn, self.cfg, ["SH000300"], verbose=False)
        self.assertTrue(any(info["aggregated"].values()))
        got = intraday.counts(self.conn)
        self.assertEqual(got[1], 242)          # 夹具给一整天的 1 分钟（含 11:30 / 15:00）
        self.assertEqual(got[30], 8)           # 聚完正好 8 根
        self.assertEqual(got[5], 48)
        self.assertEqual(got[60], 4)

    def test_aggregate_missing_is_idempotent(self):
        tasks.collect_intraday_bars(self.conn, self.cfg, ["SH000300"], verbose=False)
        again = intraday.aggregate_missing(self.conn)
        self.assertEqual(sum(again.values()), 0)     # 已经聚过就不再写

    def test_prune_keeps_only_the_latest_days(self):
        rows = [
            {"code": "SH000300", "dt": f"{day} 09:30", "period": 1, "close": 10.0,
             "volume": 100.0, "amount": 1000.0, "source": "test"}
            for day in ("2026-01-05", "2026-01-06", "2026-01-07")
        ]
        db.upsert_rows(self.conn, "bars_intraday", rows, ["code", "dt", "period"])
        self.cfg["collection"]["keep_intraday_days"] = 2
        self.assertEqual(tasks.prune_intraday(self.conn, self.cfg), 1)
        days = {row["day"] for row in db.query(
            self.conn, "SELECT DISTINCT substr(dt, 1, 10) AS day FROM bars_intraday")}
        self.assertEqual(days, {"2026-01-06", "2026-01-07"})


class IntradayApiTests(unittest.TestCase):
    """看盘页面拿分时走的就是这个接口。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        use_fixture_sources(self.cfg)
        self.cfg["_db_path"] = str(root / "t.db")
        conn = db.connect(self.cfg["_db_path"])
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        conn.close()
        self.app = server.App(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_call_fetches_then_serves_points(self):
        payload = self.app.intraday("SH000300")
        self.assertEqual(payload["code"], "SH000300")
        self.assertTrue(payload["points"])
        self.assertTrue(payload["date"].startswith("20"))
        first = payload["points"][0]
        self.assertIn("t", first)
        self.assertIn("p", first)
        self.assertIn("avg", first)                   # 均价线要有

    def test_average_line_is_cumulative(self):
        payload = self.app.intraday("SH000300")
        points = payload["points"]
        if len(points) > 3:
            # 均价应该落在当日价格区间内，不是乱数
            prices = [p["p"] for p in points]
            self.assertGreaterEqual(points[-1]["avg"], min(prices) * 0.98)
            self.assertLessEqual(points[-1]["avg"], max(prices) * 1.02)

    def test_without_a_data_source_it_reports_instead_of_crashing(self):
        # 没有支持分时的数据源时，接口要给一句人话，而不是抛异常或给个空图
        with mock.patch.object(tasks, "intraday_source", return_value=None):
            payload = self.app.intraday("SH000300")
        self.assertEqual(payload["points"], [])
        self.assertIn("message", payload)


if __name__ == "__main__":
    unittest.main()
