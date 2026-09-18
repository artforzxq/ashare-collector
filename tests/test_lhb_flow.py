"""龙虎榜与资金流：解析、落库、以及"数据源不可用时不拖垮日终"。

这两个接口的列名随上游网页调整而变，所以解析器一律**按关键字找列**；
测试用的列名故意写成真实的中文列名，改错关键字这里就会红。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import db, tasks
from collector.config import load_config, use_fixture_sources
from collector.sources.akshare_source import AkshareSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Frame:
    """假装是 akshare 返回的 DataFrame：只要有 columns 和 to_dict 就够用。"""

    def __init__(self, columns, records):
        self.columns = columns
        self._records = records

    def to_dict(self, orient="records"):
        return list(self._records)


class LhbParseTests(unittest.TestCase):
    def test_picks_columns_by_keyword(self):
        frame = _Frame(
            ["序号", "代码", "名称", "上榜日", "收盘价", "涨跌幅", "龙虎榜净买额",
             "龙虎榜买入额", "龙虎榜卖出额", "龙虎榜成交额", "净买额占总成交比", "上榜原因"],
            [{"序号": 1, "代码": "000063", "名称": "中兴通讯", "上榜日": "2026-09-15",
              "收盘价": 32.37, "涨跌幅": -10.04, "龙虎榜净买额": 1.5e8,
              "龙虎榜买入额": 3.0e8, "龙虎榜卖出额": 1.5e8, "龙虎榜成交额": 5.0e8,
              "净买额占总成交比": 4.53, "上榜原因": "日跌幅偏离值达到 7%"}],
        )
        source = AkshareSource({"sources": {"min_interval_sec": 0}})
        with mock.patch.object(source, "_ak", return_value=mock.Mock(
                stock_lhb_detail_em=lambda **kw: frame)):
            rows = source.lhb("2026-09-15", "2026-09-16")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["code"], "SZ000063")        # 自动补交易所前缀
        self.assertEqual(row["trade_date"], "2026-09-15")
        self.assertAlmostEqual(row["net_buy"], 1.5e8)
        self.assertIn("日跌幅", row["reason"])

    def test_missing_columns_is_reported_not_silent(self):
        source = AkshareSource({"sources": {"min_interval_sec": 0}})
        with mock.patch.object(source, "_ak", return_value=mock.Mock(
                stock_lhb_detail_em=lambda **kw: _Frame(["甲", "乙"], []))):
            with self.assertRaises(Exception):
                source.lhb("2026-09-15", "2026-09-16")


class FundFlowParseTests(unittest.TestCase):
    def test_parses_main_and_sub_orders(self):
        frame = _Frame(
            ["日期", "收盘价", "涨跌幅", "主力净流入-净额", "主力净流入-净占比",
             "超大单净流入-净额", "大单净流入-净额", "中单净流入-净额", "小单净流入-净额"],
            [{"日期": "2026-09-17", "收盘价": 31.87, "涨跌幅": -1.54,
              "主力净流入-净额": -3.27e8, "主力净流入-净占比": -20.02,
              "超大单净流入-净额": -2.29e8, "大单净流入-净额": -9.7e7,
              "中单净流入-净额": 5.69e7, "小单净流入-净额": 2.70e8}],
        )
        source = AkshareSource({"sources": {"min_interval_sec": 0}})
        with mock.patch.object(source, "_ak", return_value=mock.Mock(
                stock_individual_fund_flow=lambda **kw: frame)), \
                mock.patch("collector.sources.akshare_source.time.sleep", lambda *_: None):
            rows = source.fund_flow("SZ000063")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["main_net"], -3.27e8)
        self.assertAlmostEqual(rows[0]["super_net"], -2.29e8)
        self.assertAlmostEqual(rows[0]["main_ratio"], -20.02)


class CollectionTests(unittest.TestCase):
    """采集要容错：没有数据源、或数据源报错，都不能把日终任务带崩。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        use_fixture_sources(self.cfg)
        self.cfg["_db_path"] = str(root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_no_source_is_a_quiet_skip(self):
        # 注意：不能指望"夹具源没有 lhb"——_source_for 会兜底到池子里的 akshare，
        # 那样测试就真去调网络了。这里直接把"没有数据源"这件事钉死。
        with mock.patch.object(tasks, "_source_for", return_value=None):
            info = tasks.collect_lhb(self.conn, self.cfg, "2026-09-15", "2026-09-16", verbose=False)
        self.assertFalse(info["ok"])
        self.assertIn("数据源", info["message"])

    def test_source_error_is_swallowed(self):
        broken = mock.Mock()
        broken.name = "fake"                 # log_health 要写字符串
        broken.lhb.side_effect = RuntimeError("接口挂了")
        with mock.patch.object(tasks, "_source_for", return_value=broken):
            info = tasks.collect_lhb(self.conn, self.cfg, "2026-09-15", "2026-09-16", verbose=False)
        self.assertFalse(info["ok"])
        self.assertIn("接口挂了", str(info["message"]))

    def test_rows_are_written_when_the_source_works(self):
        good = mock.Mock()
        good.name = "fake"
        good.lhb.return_value = [{
            "trade_date": "2026-09-15", "code": "SZ000063", "reason": "测试原因",
            "name": "中兴通讯", "close": 32.37, "pct_chg": -10.04, "net_buy": 1.5e8,
            "buy_amount": 3e8, "sell_amount": 1.5e8, "turnover": 5e8, "net_ratio": 4.53,
        }]
        with mock.patch.object(tasks, "_source_for", return_value=good):
            info = tasks.collect_lhb(self.conn, self.cfg, "2026-09-15", "2026-09-16", verbose=False)
        self.assertTrue(info["ok"])
        row = db.query_one(self.conn, "SELECT code, net_buy, reason FROM lhb")
        self.assertEqual(row["code"], "SZ000063")
        self.assertAlmostEqual(row["net_buy"], 1.5e8)


if __name__ == "__main__":
    unittest.main()
