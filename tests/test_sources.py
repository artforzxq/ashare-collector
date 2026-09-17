"""数据源能力路由与字段清洗的离线测试（不联网）。"""

import unittest

from collector import tasks
from collector.sources.akshare_source import _num, _symbol


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
