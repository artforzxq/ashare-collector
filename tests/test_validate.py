import unittest

from collector.validate import grade_missing, merge_two_sources, validate_bars

CFG = {
    "validation": {
        "price_tol": 0.003,
        "amount_tol": 0.03,
        "jump_pct_limit": 11.0,
        "volume_anomaly_ratio": 10.0,
        "critical_codes": ["SH000300"],
    }
}


def bar(date, close, amount=1e8, pct=0.5):
    return {
        "code": "SH000300",
        "trade_date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": close,
        "volume": 1e6,
        "amount": amount,
        "pct_chg": pct,
        "source": "primary",
        "quality_flag": "ok",
    }


class MergeTests(unittest.TestCase):
    def test_conflict_needs_both_price_and_pct_to_disagree(self):
        """两条都不一致才是冲突——只看绝对价会把复权口径差异当成数据错误。"""
        primary = [bar("2026-09-01", 10.0, pct=1.0), bar("2026-09-02", 10.5, pct=5.0)]
        backup = [bar("2026-09-01", 10.0, pct=1.0), bar("2026-09-02", 10.6, pct=1.0)]
        result = merge_two_sources(primary, backup, CFG)

        self.assertEqual(result.quality_flag, "suspect")
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.rows[1]["quality_flag"], "suspect")
        self.assertEqual(result.rows[0]["quality_flag"], "ok")

    def test_impossible_pct_is_repaired_from_the_backup(self):
        """主源在除权日会拿错前收，派生出 +53% 这种不可能的涨跌幅。

        备份源那天正常 → 用它的涨跌幅把价格修正回来（价格仍用主源，保持复权基准统一），
        否则这一天会被跳变校验直接拦掉，等于凭空丢一天数据。
        """
        primary = [
            bar("2026-09-01", 10.0, pct=0.0),
            bar("2026-09-02", 15.3, pct=53.0),      # 不可能：主板上限 10%
        ]
        backup = [
            bar("2026-09-01", 10.0, pct=0.0),
            bar("2026-09-02", 9.6, pct=-4.0),       # 备份源正常
        ]
        result = merge_two_sources(primary, backup, CFG)
        fixed = result.rows[1]
        self.assertAlmostEqual(fixed["pct_chg"], -4.0, places=4)
        self.assertAlmostEqual(fixed["pre_close"], 15.3 / 0.96, places=3)
        self.assertEqual(fixed["quality_flag"], "suspect")
        self.assertTrue(any("修正" in note for note in result.notes))
        # 修完就不该再被跳变校验拦下
        checked = validate_bars(result.rows, CFG)
        self.assertEqual(len(checked.blocked), 0)

    def test_rebase_difference_is_a_note_not_a_conflict(self):
        """绝对价差 >0.3% 但涨跌幅一致 → 复权基准不同，记说明，不算冲突。

        实测：同一只票两源收盘价能差 2.7%（各自的前复权基准不同），
        可每天的涨跌幅差不到 0.1 个百分点——这不是数据错误。
        """
        primary = [bar("2026-09-01", 10.0, pct=0.5), bar("2026-09-02", 10.05, pct=0.5)]
        backup = [bar("2026-09-01", 10.3, pct=0.5), bar("2026-09-02", 10.35, pct=0.5)]
        result = merge_two_sources(primary, backup, CFG)

        self.assertEqual(result.quality_flag, "ok")      # 没有冲突，只有说明
        self.assertEqual(result.conflicts, [])
        self.assertTrue(any("复权" in note for note in result.notes))

    def test_small_difference_is_ignored(self):
        primary = [bar("2026-09-01", 10.0)]
        backup = [bar("2026-09-01", 10.01)]  # 差 0.1% 以内
        result = merge_two_sources(primary, backup, CFG)
        self.assertEqual(result.quality_flag, "ok")

    def test_backup_only_date_is_written_as_suspect(self):
        primary = [bar("2026-09-01", 10.0)]
        backup = [bar("2026-09-01", 10.0), bar("2026-09-02", 10.2)]
        result = merge_two_sources(primary, backup, CFG)
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[1]["quality_flag"], "suspect")


class BarCheckTests(unittest.TestCase):
    def test_jump_is_blocked(self):
        rows = [bar("2026-09-01", 10.0, pct=12.5)]
        result = validate_bars(rows, CFG)
        self.assertEqual(result.quality_flag, "blocked")
        self.assertEqual(result.rows[0]["quality_flag"], "blocked")
        self.assertEqual(len(result.blocked), 1)

    def test_volume_spike_is_flagged_not_blocked(self):
        rows = [bar(f"2026-08-{day:02d}", 10.0, amount=1e8) for day in range(1, 21)]
        rows.append(bar("2026-09-01", 10.0, amount=3e9))  # 30 倍
        result = validate_bars(rows, CFG)
        self.assertEqual(result.quality_flag, "ok")
        self.assertEqual(len(result.anomalies), 1)


class MissingGradeTests(unittest.TestCase):
    def test_critical_code_missing_freezes_alerts(self):
        level, _ = grade_missing(["SH000300"], ["SH000300"])
        self.assertEqual(level, "L3")

    def test_single_and_multiple_missing(self):
        self.assertEqual(grade_missing(["SH510300"], ["SH000300"])[0], "L1")
        self.assertEqual(grade_missing(["SH510300", "SH510050"], ["SH000300"])[0], "L2")
        self.assertEqual(grade_missing([], [])[0], "ok")


class JumpLimitByBoardTests(unittest.TestCase):
    """跳变阈值要按"那天法定能涨多少"算，不能一刀切 11%。

    以前创业板/科创板正常的 20%、北交所的 30%、新股上市头几天的不设限，
    全被当成脏数据挡住——A 股新股前几个交易日本来就没有涨跌幅限制。
    """

    def _rows(self, pct: float, day: str = "2026-09-18"):
        return [{"trade_date": day, "code": "SZ300750", "close": 10.0 * (1 + pct / 100),
                 "pre_close": 10.0, "pct_chg": pct, "amount": 1e8}]

    def test_gem_twenty_percent_is_legal(self):
        result = validate_bars(self._rows(20.0), CFG, code="SZ300750", name="宁德时代")
        self.assertFalse(result.blocked)
        self.assertEqual(result.quality_flag, "ok")

    def test_gem_thirty_percent_is_still_blocked(self):
        result = validate_bars(self._rows(30.0), CFG, code="SZ300750", name="宁德时代")
        self.assertTrue(result.blocked)

    def test_main_board_still_blocks_eleven_plus(self):
        rows = [{"trade_date": "2026-09-18", "code": "SH600000", "close": 11.5,
                 "pre_close": 10.0, "pct_chg": 15.0, "amount": 1e8}]
        result = validate_bars(rows, CFG, code="SH600000", name="浦发银行")
        self.assertTrue(result.blocked)

    def test_new_stock_first_days_are_not_blocked(self):
        """创业板新股第 2 个交易日涨 60% 是合法的（前 5 日不设涨跌幅）。"""
        rows = self._rows(60.0)
        result = validate_bars(rows, CFG, code="SZ301888", name="次新乙",
                               trading_days={"2026-09-18": 2})
        self.assertFalse(result.blocked)

    def test_new_stock_absurd_move_is_still_blocked(self):
        rows = self._rows(900.0)
        result = validate_bars(rows, CFG, code="SZ301888", name="次新乙",
                               trading_days={"2026-09-18": 2})
        self.assertTrue(result.blocked)

    def test_without_a_code_it_keeps_the_old_threshold(self):
        result = validate_bars(self._rows(20.0), CFG)
        self.assertTrue(result.blocked)          # 没给标的身份 → 退回 11%


if __name__ == "__main__":
    unittest.main()
