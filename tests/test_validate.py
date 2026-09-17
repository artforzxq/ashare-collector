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
    def test_conflict_beyond_tolerance_is_flagged(self):
        primary = [bar("2026-09-01", 10.0), bar("2026-09-02", 10.5)]
        backup = [bar("2026-09-01", 10.0), bar("2026-09-02", 10.6)]  # 差 0.95% > 0.3%
        result = merge_two_sources(primary, backup, CFG)

        self.assertEqual(result.quality_flag, "suspect")
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.rows[1]["quality_flag"], "suspect")
        self.assertEqual(result.rows[0]["quality_flag"], "ok")

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


if __name__ == "__main__":
    unittest.main()
