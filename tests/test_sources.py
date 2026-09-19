"""数据源能力路由与字段清洗的离线测试（不联网）。"""

import unittest

from collector import tasks
from collector.sources.akshare_source import _num, _symbol
from collector.sources.baostock_source import _directory_rows as baostock_rows
from collector.sources.base import classify_symbol, exchange_of, normalize_symbol
from collector.sources.sina_source import _directory_rows as sina_rows


class _FakeSource:
    def __init__(self, name, capabilities):
        self.name = name
        self.capabilities = set(capabilities)


class CapabilityRoutingTests(unittest.TestCase):
    """上层按 capabilities 挑源：主源不会的能力要能落到备份源上。"""

    def setUp(self):
        self.pool = [
            _FakeSource("baostock", {"daily_bars", "trade_calendar"}),
            _FakeSource("akshare", {"daily_bars", "market_snapshot", "etf_shares"}),
        ]

    def test_falls_through_to_capable_source(self):
        self.assertEqual(tasks._source_for(self.pool, "market_snapshot").name, "akshare")
        self.assertEqual(tasks._source_for(self.pool, "etf_shares").name, "akshare")

    def test_prefers_earlier_source_when_both_capable(self):
        self.assertEqual(tasks._source_for(self.pool, "trade_calendar").name, "baostock")
        self.assertEqual(tasks._source_for(self.pool, "daily_bars").name, "baostock")

    def test_returns_none_when_nobody_capable(self):
        self.assertIsNone(tasks._source_for(self.pool, "margin"))


class AkshareFieldTests(unittest.TestCase):
    def test_num_handles_dirty_values(self):
        self.assertEqual(_num("1,234.5"), 1234.5)
        self.assertEqual(_num(3), 3.0)
        self.assertIsNone(_num("—"))
        self.assertIsNone(_num(""))
        self.assertIsNone(_num(None))
        self.assertIsNone(_num(True))

    def test_symbol_normalizes_exchange_suffix_and_padding(self):
        self.assertEqual(_symbol(510300), "510300")
        self.assertEqual(_symbol("510300.SH"), "510300")
        self.assertEqual(_symbol("000300"), "000300")
        self.assertEqual(_symbol(300), "000300")


if __name__ == "__main__":
    unittest.main()

class SymbolTests(unittest.TestCase):
    """代码归一化与分类：指数和个股会撞号，全靠交易所前缀区分。"""

    def test_normalize_symbol_handles_every_writing_style(self):
        for raw in ("sh.600000", "sh600000", "SH600000", "600000", "600000.SH"):
            self.assertEqual(normalize_symbol(raw), "600000", raw)

    def test_classifies_stocks_funds_and_indices(self):
        cases = {
            "sh.600000": "stock",      # 沪市主板
            "sz.300750": "stock",      # 创业板
            "sz.302132": "stock",      # 创业板次新段
            "bj.920000": "stock",      # 北交所
            "sh.510300": "etf",        # 沪深300ETF
            "sh.588000": "etf",        # 科创50ETF
            "sz.159919": "etf",        # 300ETF
            "sz.161725": "etf",        # LOF
            "sh.000300": "index",      # 沪深300指数
            "sz.399006": "index",      # 创业板指
            "sh.113050": "other",      # 可转债
            "sz.200011": "other",      # B 股
            "sh.900901": "other",      # B 股
        }
        for raw, want in cases.items():
            self.assertEqual(classify_symbol(raw), want, raw)

    def test_same_number_different_exchange_is_not_the_same_thing(self):
        self.assertEqual(classify_symbol("sh.000001"), "index")    # 上证指数
        self.assertEqual(classify_symbol("sz.000001"), "stock")    # 平安银行
        self.assertEqual(classify_symbol("000001"), "stock")       # 不带前缀按号段判
        self.assertEqual(exchange_of("000001"), "SZ")


class DirectoryRowTests(unittest.TestCase):
    """两个源的原始行 → 落库用的清单。"""

    def test_baostock_keeps_stocks_funds_and_indices_but_drops_bonds(self):
        records = [
            {"code": "sh.600000", "code_name": "浦发银行"},
            {"code": "sh.510300", "code_name": "沪深300ETF"},
            {"code": "sh.000300", "code_name": "沪深300指数"},
            {"code": "sz.399006", "code_name": "创业板指"},
            {"code": "sh.113050", "code_name": "南银转债"},
        ]
        rows = baostock_rows(records)
        self.assertEqual([row["code"] for row in rows], ["SH600000", "SH510300", "SH000300", "SZ399006"])
        self.assertEqual([row["type"] for row in rows], ["stock", "etf", "index", "index"])

    def test_sina_rows_are_deduped_and_classified(self):
        items = [
            {"symbol": "bj920000", "name": "安徽凤凰"},
            {"symbol": "sh600000", "name": "浦发银行"},
            {"symbol": "sh600000", "name": "浦发银行"},
            {"symbol": "sz159919", "name": "沪深300ETF"},
        ]
        rows = sina_rows(items)
        self.assertEqual([row["code"] for row in rows], ["BJ920000", "SH600000", "SZ159919"])
        self.assertEqual([row["type"] for row in rows], ["stock", "stock", "etf"])
