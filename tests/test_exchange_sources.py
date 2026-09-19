"""交易所适配器：上交所（沪市份额）与深交所（深市份额 + 净值）。

全部离线——HTTP 那一层被替换掉，测的是解析与口径：
  · 上交所给的是**万份**，深交所也是**万份**，落库都要换算成**份**；
  · 上交所按日披露（正式），深交所是"当前快照"（估算），两者要能区分；
  · 各管各的市场：上交所只给沪市、深交所只给深市，不能互相污染。
"""

import unittest

from collector.sources.sse_source import SHARES_UNIT as SSE_UNIT
from collector.sources.sse_source import SseSource, parse_rows as sse_rows
from collector.sources.szse_source import (
    SzseSource,
    number_from_html,
    parse_nav,
    parse_share_row,
)


class SseTests(unittest.TestCase):
    def _payload(self, stat_date="2026-09-18"):
        return {"result": [
            {"SEC_CODE": "510500", "SEC_NAME": "500ETF", "TOT_VOL": "572,256.86", "STAT_DATE": stat_date},
            {"SEC_CODE": "999999", "SEC_NAME": "不在池子里", "TOT_VOL": "1.00", "STAT_DATE": stat_date},
        ]}

    def test_wan_to_shares_and_disclosure_flag(self):
        rows = sse_rows(self._payload(), "2026-09-18", {"510500": "SH510500"})
        self.assertEqual(len(rows), 1)                       # 池子外的代码不进
        row = rows[0]
        self.assertEqual(row["code"], "SH510500")
        self.assertEqual(row["shares"], 572_256.86 * SSE_UNIT)
        self.assertEqual(row["is_estimated"], 0)             # 当天披露 → 正式数字

    def test_previous_disclosure_is_marked_estimated(self):
        rows = sse_rows(self._payload(stat_date="2026-09-17"), "2026-09-18", {"510500": "SH510500"})
        self.assertEqual(rows[0]["is_estimated"], 1)         # 不是当天的数 → 标估算

    def test_missing_value_is_skipped(self):
        payload = {"result": [{"SEC_CODE": "510500", "TOT_VOL": "", "STAT_DATE": "2026-09-18"}]}
        self.assertEqual(sse_rows(payload, "2026-09-18", {"510500": "SH510500"}), [])

    def test_only_shanghai_codes_are_asked_for(self):
        source = SseSource({})
        source._query = lambda day, page_size=2000: self._payload()
        rows = source.etf_shares(["SZ159919"], "2026-09-18")
        self.assertEqual(rows, [])                            # 深市不归上交所管


class SzseTests(unittest.TestCase):
    def test_number_is_extracted_from_the_html_cell(self):
        self.assertEqual(number_from_html("<a href='x'>632,901.66</a>"), 632901.66)
        self.assertEqual(number_from_html("1,985,945.49"), 1985945.49)
        self.assertIsNone(number_from_html("查看"))

    def test_share_row_converts_wan_to_shares(self):
        item = {"jjlb": "ETF", "dqgm": "<a>632,901.66</a>"}
        row = parse_share_row(item, "SZ159919", "2026-09-18")
        self.assertEqual(row["shares"], 6_329_016_600.0)      # 632901.66 万份
        self.assertEqual(row["is_estimated"], 1)              # 交易所的"当前规模"仅供参考

    def test_non_etf_rows_are_dropped(self):
        self.assertIsNone(parse_share_row({"jjlb": "LOF", "dqgm": "<a>1.00</a>"}, "SZ159919", "2026-09-18"))
        self.assertIsNone(parse_share_row({"jjlb": "ETF", "dqgm": ""}, "SZ159919", "2026-09-18"))

    def test_nav_takes_the_latest_row(self):
        payload = {"data": [
            {"nav_date": "2026-09-17", "fund_code": "159919", "nav_per_share": "4.7306"},
            {"nav_date": "2026-09-16", "fund_code": "159919", "nav_per_share": "4.7514"},
        ]}
        self.assertEqual(parse_nav(payload), ("2026-09-17", 4.7306))
        self.assertEqual(parse_nav({"data": []}), (None, None))

    def test_only_shenzhen_codes_are_asked_for(self):
        source = SzseSource({})
        self.assertEqual(source.etf_shares(["SH510500"], "2026-09-18"), [])

    def test_source_combines_shares_and_nav(self):
        source = SzseSource({})

        def fake_get(**params):
            if params.get("CATALOGID") == "1000_lf":
                return {"data": [{"sys_key": "<a>159919</a>", "jjlb": "ETF", "dqgm": "<a>632,901.66</a>"}]}
            return {"data": [{"nav_date": "2026-09-17", "nav_per_share": "4.7306"}]}

        source._get = fake_get
        rows = source.etf_shares(["SZ159919"], "2026-09-18")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["nav"], 4.7306)
        self.assertEqual(row["assets"], round(6_329_016_600.0 * 4.7306, 2))


if __name__ == "__main__":
    unittest.main()
