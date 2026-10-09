"""三个"孤儿能力"的备胎源：深交所两融 / 深交所龙虎榜 / 新浪资金流。

为什么单独一组测试：这三个能力原来都只有 akshare 一个源，而 akshare 的
资金流走东财 push2his（这条线路上被切）——等于"有接口、没数据"。
备胎的解析口径一旦写错（单位、字段名），错误会静默地进库，所以这里逐条钉死。
"""

import tempfile
import json
import unittest
from pathlib import Path
from unittest import mock

from collector import db, tasks
from collector.config import load_config
from collector.sources.sina_source import SinaSource
from collector.sources.szse_source import SzseSource
from collector.sources import jin10_source
from collector.sources.jin10_source import Jin10Source
from collector.sources.base import DataSourceError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _szse_margin_payload():
    """深交所两融汇总（tab1）的真实结构，数字照抄 2026-09-30 深市。"""
    return [{
        "metadata": {"tabkey": "tab1", "name": "融资融券交易总量", "subname": "2026-09-30"},
        "data": [{"jrrzmr": "515.27", "jrrzye": "12,358.98", "jrrjmc": "0.17",
                  "jrrjyl": "11.87", "jrrjye": "105.03", "jrrzrjye": "12,464.01"}],
    }]


class SzseMarginTests(unittest.TestCase):
    def test_summary_units_are_yi_and_identity_holds(self):
        source = SzseSource({"sources": {"min_interval_sec": 0}})
        with mock.patch.object(source, "_www_get", return_value=_szse_margin_payload()):
            rows = source.margin("2026-09-30")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["market"], "SZ")
        self.assertEqual(row["data_date"], "2026-09-30")
        # 汇总表的金额单位是**亿元**：12,358.98 亿 → 1.2359e12 元
        self.assertAlmostEqual(row["financing_balance"], 12_358.98e8, places=0)
        self.assertAlmostEqual(row["securities_lending"], 105.03e8, places=0)
        # 恒等式：融资余额 + 融券余额 = 融资融券余额（实测差额 0.01 亿，四舍五入）
        self.assertAlmostEqual(row["total"], row["financing_balance"] + row["securities_lending"],
                               delta=2e6)

    def test_not_published_yet_walks_back(self):
        """T+1 披露：当天没有就往前找，找到哪天记哪天。"""
        source = SzseSource({"sources": {"min_interval_sec": 0}})
        calls = []

        def fake(params, referer):
            calls.append(params["txtDate"])
            if params["txtDate"] == "2026-10-08":
                return [{"metadata": {"tabkey": "tab1"}, "data": [{"jrrzye": "1.00", "jrrjye": "0.00",
                                                                   "jrrzrjye": "1.00", "jrrzmr": "0.10"}]}]
            return [{"metadata": {"tabkey": "tab1"}, "data": []}]

        with mock.patch.object(source, "_www_get", side_effect=fake):
            rows = source.margin("2026-10-09")
        self.assertEqual(rows[0]["data_date"], "2026-10-08")     # 不是 10-09
        self.assertIn("2026-10-09", calls)                        # 先问当天


class SzseLhbTests(unittest.TestCase):
    def _source(self):
        return SzseSource({"sources": {"min_interval_sec": 0}})

    def test_summary_plus_seats_gives_net_buy(self):
        source = self._source()

        def fake(params, referer):
            if params.get("CATALOGID") == "1842_xxpl":
                return [{"metadata": {"tabkey": "tab1", "pagecount": 1},
                         "data": [{"dqrq": "2026-09-30", "zqdm": "000002",
                                   "zqjc": "万&nbsp;&nbsp;科Ａ", "cjje": "51.58",
                                   "plyy": "日价格振幅达到19.89%",
                                   "bz": "a-param='/ShowReport/data?ZBDM=0902'"}]}]
            return [{"metadata": {"tabkey": "tab2"},
                     "data": [{"mmlb": "买1", "zsmc": "深股通专用",
                               "mrje": "212,786,510", "mcje": "121,614,254"},
                              {"mmlb": "卖1", "zsmc": "某营业部",
                               "mrje": "0", "mcje": "50,000,000"}]}]

        with mock.patch.object(source, "_www_get", side_effect=fake):
            rows = source.lhb("2026-09-30", "2026-09-30")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["code"], "SZ000002")
        self.assertEqual(row["name"], "万科Ａ")                    # &nbsp; 要清掉
        self.assertIn("振幅", row["reason"])
        self.assertAlmostEqual(row["turnover"], 51.58e8, places=0)  # 汇总表是亿元
        # 口径同东财：买入额 = 买五席位的买入金额合计；卖出额 = 卖五席位的卖出金额合计。
        # 买1 那个席位自己也有卖出（121,614,254），但那不算"卖出额"。
        self.assertAlmostEqual(row["buy_amount"], 212_786_510, places=0)
        self.assertAlmostEqual(row["sell_amount"], 50_000_000, places=0)
        self.assertAlmostEqual(row["net_buy"], 212_786_510 - 50_000_000, places=0)

    def test_seat_failure_keeps_the_row_without_money(self):
        """一只票的席位取不到，不能把它整条丢掉——上榜这件事本身有价值。"""
        from collector.sources.base import DataSourceError
        source = self._source()

        def fake(params, referer):
            if params.get("CATALOGID") == "1842_xxpl":
                return [{"metadata": {"tabkey": "tab1", "pagecount": 1},
                         "data": [{"dqrq": "2026-09-30", "zqdm": "000011", "zqjc": "深物业A",
                                   "cjje": "9.71", "plyy": "日价格涨幅偏离值", "bz": ""}]}]
            raise DataSourceError("席位接口挂了")

        with mock.patch.object(source, "_www_get", side_effect=fake):
            rows = source.lhb("2026-09-30", "2026-09-30")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["net_buy"])
        self.assertAlmostEqual(rows[0]["turnover"], 9.71e8, places=0)


class SinaFundFlowTests(unittest.TestCase):
    def test_parses_main_and_super_orders(self):
        body = ('[{"opendate":"2026-09-30","trade":"1258.6200","changeratio":"0.0186471",'
                '"turnover":"30.6628","netamount":"741042739.9400","ratioamount":"0.154473",'
                '"r0_net":"795888790.6600","r0_ratio":"0.16590533"}]')
        response = mock.Mock(status_code=200, text=body)
        source = SinaSource({"sources": {"min_interval_sec": 0}})
        with mock.patch.object(source, "_requests", return_value=mock.Mock(get=lambda *a, **k: response)):
            rows = source.fund_flow("SH600519", days=1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["code"], "SH600519")
        self.assertAlmostEqual(row["main_net"], 741042739.94)
        self.assertAlmostEqual(row["super_net"], 795888790.66)
        self.assertAlmostEqual(row["pct_chg"], 1.8647, places=3)      # 小数 → %
        self.assertAlmostEqual(row["main_ratio"], 15.4473, places=3)  # 小数 → %
        self.assertIsNone(row["large_net"])                            # 新浪只给两档


class Jin10MarginTests(unittest.TestCase):
    """金十备胎：沪市两融唯一能用的非官方来源（上交所接口在本机网络会 500）。"""

    KEYS = [{"name": "融资买入额", "unit": "元"}, {"name": "融资余额", "unit": "元"},
            {"name": "融券卖出量", "unit": "股"}, {"name": "融券余量", "unit": "股"},
            {"name": "融券余额", "unit": "元"}, {"name": "融资融券余额", "unit": "元"}]

    def _payload(self, values):
        return json.dumps({"keys": self.KEYS, "values": values}).encode()

    def _source_with(self, values, keys=None):
        source = Jin10Source({"sources": {"min_interval_sec": 0}})
        body = json.dumps({"keys": keys or self.KEYS, "values": values}).encode()
        response = mock.MagicMock()
        response.read.return_value = body
        response.__enter__ = lambda self: response
        response.__exit__ = lambda *args: False
        return source, mock.patch.object(jin10_source.urllib.request, "urlopen", return_value=response)

    def test_returns_both_markets_in_yuan(self):
        # 数字照抄实测：沪市 2026-10-08 融资 1.2984 万亿、融券 189 亿
        values = {"2026-10-08": [75710415745, 1298438300567, 70677151, 3251744595,
                                 18936692018, 1317374992585]}
        source, patched = self._source_with(values)
        with patched:
            rows = source.margin("2026-10-08")
        self.assertEqual({row["market"] for row in rows}, {"SH", "SZ"})
        shanghai = next(row for row in rows if row["market"] == "SH")
        self.assertAlmostEqual(shanghai["financing_balance"], 1298438300567.0)
        self.assertAlmostEqual(shanghai["securities_lending"], 18936692018.0)
        self.assertEqual(shanghai["source"], "jin10")

    def test_unit_change_is_caught_not_silently_written(self):
        """上游哪天把单位从元改成亿元，必须炸出来——静默写库会让数字差 1 亿倍。"""
        keys = [dict(item) for item in self.KEYS]
        keys[1] = {"name": "融资余额", "unit": "亿元"}
        source, patched = self._source_with({}, keys=keys)
        with patched:
            with self.assertRaises(DataSourceError) as caught:
                source.margin("2026-10-08")
        self.assertIn("单位", str(caught.exception))

    def test_identity_break_is_caught(self):
        """融资余额 + 融券余额 对不上融资融券余额 → 数据有问题，不写库。"""
        values = {"2026-10-08": [1.0, 100.0, 1.0, 1.0, 5.0, 999.0]}   # 100 + 5 != 999
        source, patched = self._source_with(values)
        with patched:
            with self.assertRaises(DataSourceError) as caught:
                source.margin("2026-10-08")
        self.assertIn("恒等式", str(caught.exception))

    def test_missing_day_falls_back_to_the_previous_one(self):
        values = {"2026-10-07": [1.0, 100.0, 1.0, 1.0, 5.0, 105.0]}
        source, patched = self._source_with(values)
        with patched:
            rows = source.margin("2026-10-08")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["data_date"], "2026-10-07")


class FundFlowFallbackTests(unittest.TestCase):
    """资金流是"互为备胎"：第一个源失败要换下一个，而不是整批失败。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_second_source_is_used_when_the_first_fails(self):
        calls = []

        class Broken:
            name = "broken"
            capabilities = {"fund_flow"}

            def fund_flow(self, code):
                calls.append(("broken", code))
                raise RuntimeError("东财被切")

        class Backup:
            name = "backup"
            capabilities = {"fund_flow"}

            def fund_flow(self, code):
                calls.append(("backup", code))
                return [{"code": code, "trade_date": "2026-09-30", "main_net": 1.0, "source": self.name}]

        with mock.patch.object(tasks, "_source_pool", return_value=[Broken(), Backup()]):
            result = tasks.collect_fund_flow(self.conn, self.cfg, codes=["SH600519"], verbose=False)
        self.assertEqual(result["rows"], 1)
        self.assertIn(("backup", "SH600519"), calls)          # 真的降级了
        saved = db.query_one(self.conn, "SELECT source FROM fund_flow WHERE code='SH600519'")
        self.assertEqual(saved["source"], "backup")

    def test_all_sources_failing_is_reported_per_code(self):
        class Broken:
            name = "broken"
            capabilities = {"fund_flow"}

            def fund_flow(self, code):
                raise RuntimeError("不通")

        with mock.patch.object(tasks, "_source_pool", return_value=[Broken()]):
            result = tasks.collect_fund_flow(self.conn, self.cfg, codes=["SH600000"], verbose=False)
        self.assertEqual(result["rows"], 0)
        self.assertEqual(result["failed"], ["SH600000"])


if __name__ == "__main__":
    unittest.main()
