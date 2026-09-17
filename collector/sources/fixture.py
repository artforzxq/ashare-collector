"""离线夹具源：确定性生成行情，用于在没有网络/未装数据源时跑通全流程。

主标的 SH000300 的价格脚本刻意做成：拉升 → 高位横盘 40 日（缩量）→ 放量突破 → 回踩支撑带。
用来验证状态机（震荡 → 上升）、迟滞、缩量蓄势、突破确认、回踩提醒这几条链路。
"""

from __future__ import annotations

import csv
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from .base import BaseSource, DataSourceError

# 2026 年主要休市日（占位，接入真实交易日历后替换）
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-06", "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}


def _seed(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


class _Rng:
    """确定性伪随机（线性同余），不依赖 numpy，保证每次运行结果一致。"""

    def __init__(self, seed: int):
        self.state = seed % (2**31) or 1

    def next(self) -> float:
        self.state = (1103515245 * self.state + 12345) % (2**31)
        return self.state / (2**31)

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.next()


def trading_days(end: str | date, count: int) -> list[str]:
    """从 end 往前取 count 个交易日（工作日，排除占位休市日）。"""
    end_date = datetime.strptime(str(end)[:10], "%Y-%m-%d").date() if not isinstance(end, date) else end
    days: list[str] = []
    cursor = end_date
    while len(days) < count:
        text = cursor.strftime("%Y-%m-%d")
        if cursor.weekday() < 5 and text not in HOLIDAYS_2026:
            days.append(text)
        cursor -= timedelta(days=1)
    return list(reversed(days))


class FixtureSource(BaseSource):
    name = "fixture"
    capabilities = {"daily_bars", "market_snapshot", "intraday_snapshot", "etf_shares", "margin", "trade_calendar"}

    def __init__(self, cfg: dict | None = None, perturb: bool = False, days: int = 260):
        super().__init__(cfg)
        self.perturb = perturb
        self.days = days
        end = (cfg or {}).get("_fixture_end_date") or date.today().strftime("%Y-%m-%d")
        self._dates = trading_days(end, days)
        self._bars_cache: dict[str, list[dict]] = {}

    # ---- 价格脚本 ----
    def _returns(self, code: str, kind: str) -> list[float]:
        rng = _Rng(_seed(code))
        total = len(self._dates)
        rally_end, consol_end, breakout_end = 180, 220, 250
        prefix = code[:2]
        returns: list[float] = []

        if code == "SH000300" or kind == "etf":
            drift = 0.0016 if code == "SH000300" else 0.0014
            for i in range(total):
                if i < rally_end:
                    daily = drift + rng.uniform(-0.012, 0.014)
                elif i < consol_end:
                    daily = rng.uniform(-0.011, 0.011) * 0.6  # 高位横盘，振幅收敛
                elif i < breakout_end:
                    daily = 0.009 + rng.uniform(-0.008, 0.012)  # 放量突破
                else:
                    daily = -0.004 + rng.uniform(-0.012, 0.006)  # 突破后回踩支撑带
                returns.append(daily)
        elif kind == "index":
            for _ in range(total):
                returns.append(0.0006 + rng.uniform(-0.010, 0.011))
        else:
            for _ in range(total):
                returns.append(rng.uniform(-0.022, 0.020))
        return returns

    def _volume_multiplier(self, code: str, index: int, kind: str = "stock") -> float:
        if kind not in ("index", "etf") and code != "SH000300":
            return 1.0
        if index < 180:
            return 1.0
        if index < 220:
            return 0.42 - 0.10 * ((index - 180) / 40.0)   # 缩量蓄势
        if index < 250:
            return 2.30                                    # 放量突破
        return 0.85                                        # 回踩缩量

    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        if code not in self._bars_cache:
            self._bars_cache[code] = self._build_bars(code, kind)
        return [row for row in self._bars_cache[code] if start <= row["trade_date"] <= end]

    def _build_bars(self, code: str, kind: str) -> list[dict]:
        returns = self._returns(code, kind)
        rng = _Rng(_seed(code + "-noise"))
        base_price = 3200.0 if kind == "index" else (3.8 if kind == "etf" else 12.0)
        base_volume = 2.4e8 if kind == "index" else 3.0e8
        price = base_price
        rows: list[dict] = []

        for index, trade_date in enumerate(self._dates):
            pre_close = price
            price = max(0.5, price * (1 + returns[index]))
            # 备份源的一致性偏移：只在单日制造分歧，且不改变价格水平（否则后续每天都算冲突）
            close_price = price
            if self.perturb and code == "SH000300" and index == len(self._dates) - 12:
                close_price = price * 1.009

            open_price = pre_close * (1 + rng.uniform(-0.004, 0.004))
            high = max(open_price, close_price) * (1 + rng.uniform(0.000, 0.006))
            low = min(open_price, close_price) * (1 - rng.uniform(0.000, 0.006))
            volume = base_volume * self._volume_multiplier(code, index, kind) * rng.uniform(0.85, 1.15)
            amount = volume * close_price

            rows.append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "open": round(open_price, 4),
                    "high": round(high, 4),
                    "low": round(low, 4),
                    "close": round(close_price, 4),
                    "pre_close": round(pre_close, 4),
                    "volume": round(volume, 0),
                    "amount": round(amount, 0),
                    "turnover_rate": round(0.6 + rng.uniform(-0.3, 0.9), 4),
                    "pct_chg": round((close_price / pre_close - 1) * 100, 4) if pre_close else None,
                    "adj_factor": 1.0,
                    "close_adj": round(close_price, 4),
                    "source": self.name,
                    "quality_flag": "ok",
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return rows

    # ---- 全市场快照（用于广度） ----
    def market_snapshot(self, trade_date: str | None = None) -> list[dict]:
        target = trade_date or self._dates[-1]
        index_rows = self._build_bars("SH000300", "index")
        index_pct = next((r["pct_chg"] for r in index_rows if r["trade_date"] == target), 0.0) or 0.0
        rng = _Rng(_seed("market" + target))
        snapshot = []
        for i in range(400):
            pct = index_pct * 0.9 + rng.uniform(-4.2, 4.2)
            pct = max(-10.0, min(10.0, pct))
            # 开高低用独立的随机源，免得改动这里影响到广度那套数字
            noise = _Rng(_seed(f"ohlc{target}{i}"))
            close = round(rng.uniform(3, 60), 2)
            pre_close = close / (1 + pct / 100) if pct != -100 else close
            open_price = round(pre_close * (1 + noise.uniform(-0.01, 0.01)), 2)
            snapshot.append(
                {
                    "code": f"SZ{i:06d}",
                    "trade_date": target,
                    "open": open_price,
                    "high": round(max(open_price, close) * (1 + noise.uniform(0, 0.008)), 2),
                    "low": round(min(open_price, close) * (1 - noise.uniform(0, 0.008)), 2),
                    "close": close,
                    "pre_close": round(pre_close, 2),
                    "volume": round(rng.uniform(1e6, 5e7), 0),
                    "pct_chg": round(pct, 3),
                    "amount": round(rng.uniform(2e7, 9e8), 0),
                }
            )
        return snapshot

    def intraday_snapshot(self, codes) -> list[dict]:
        last = self._dates[-1]
        rng = _Rng(_seed("intraday" + datetime.now().strftime("%Y%m%d%H%M")))
        rows = []
        for code in codes:
            bars = self.daily_bars(code, self._dates[0], last)
            if not bars:
                continue
            base = bars[-1]["close"]
            rows.append(
                {
                    "code": code,
                    "dt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "close": round(base * (1 + rng.uniform(-0.006, 0.006)), 4),
                }
            )
        return rows

    def etf_shares(self, codes, trade_date: str) -> list[dict]:
        rows = []
        for code in codes:
            series = self._dates
            if trade_date not in series:
                continue
            index = series.index(trade_date)
            shares = 1.0e9 * (1 + 0.002 * index)
            if 180 <= index < 220:
                shares *= 1.03 + 0.01 * ((index - 180) / 40.0)  # 疑似托底：份额在横盘期抬升
            bars = self.daily_bars(code, self._dates[0], trade_date)
            close = bars[-1]["close"] if bars else None
            rows.append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "shares": round(shares, 0),
                    "nav": close,
                    "close": close,
                    "premium_rate": 0.0,
                    "assets": round(shares * (close or 0), 0),
                    "is_estimated": 0,
                    "source": self.name,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return rows

    def margin(self, trade_date: str) -> list[dict]:
        rng = _Rng(_seed("margin" + trade_date))
        previous = self._dates[max(0, self._dates.index(trade_date) - 1)] if trade_date in self._dates else trade_date
        return [
            {
                "trade_date": trade_date,
                "market": market,
                "data_date": previous,
                "fetched_date": trade_date,
                "financing_balance": round(rng.uniform(8000, 12000) * 1e8, 0),
                "securities_lending": round(rng.uniform(300, 700) * 1e8, 0),
                "total": round(rng.uniform(8300, 12700) * 1e8, 0),
                "net_buy": round(rng.uniform(-60, 80) * 1e8, 0),
                "source": self.name,
            }
            for market in ("SH", "SZ")
        ]

    def trade_calendar(self, start: str, end: str) -> list[dict]:
        days = [d for d in trading_days(end, 400) if d >= start]
        rows = []
        for index, day in enumerate(days):
            rows.append(
                {
                    "trade_date": day,
                    "is_trading_day": 1,
                    "prev_trade_date": days[index - 1] if index else None,
                    "next_trade_date": days[index + 1] if index + 1 < len(days) else None,
                }
            )
        return rows


class FixtureAltSource(FixtureSource):
    """备份源：与主源同源但含一处人为分歧，用来验证交叉校验是否生效。"""

    name = "fixture_alt"

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg, perturb=True)


class CsvSource(BaseSource):
    """回放本地导出的行情文件：fixtures/daily.csv，列名 code,date,open,high,low,close,volume,amount。"""

    name = "csv"
    capabilities = {"daily_bars"}

    def __init__(self, cfg: dict | None = None, path: str | None = None):
        super().__init__(cfg)
        root = Path((cfg or {}).get("_project_root", "."))
        self.path = Path(path) if path else root / "fixtures" / "daily.csv"

    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        if not self.path.exists():
            raise DataSourceError(f"找不到回放文件：{self.path}")
        rows = []
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            for record in csv.DictReader(handle):
                if record.get("code") != code:
                    continue
                trade_date = str(record.get("date") or record.get("trade_date"))[:10]
                if not (start <= trade_date <= end):
                    continue
                rows.append(
                    {
                        "code": code,
                        "trade_date": trade_date,
                        "open": float(record["open"]),
                        "high": float(record["high"]),
                        "low": float(record["low"]),
                        "close": float(record["close"]),
                        "pre_close": None,
                        "volume": float(record.get("volume") or 0),
                        "amount": float(record.get("amount") or 0),
                        "turnover_rate": None,
                        "pct_chg": None,
                        "adj_factor": 1.0,
                        "close_adj": float(record["close"]),
                        "source": self.name,
                    }
                )
        rows.sort(key=lambda r: r["trade_date"])
        for index, row in enumerate(rows):
            if index:
                row["pre_close"] = rows[index - 1]["close"]
                row["pct_chg"] = round((row["close"] / row["pre_close"] - 1) * 100, 4)
        return rows
