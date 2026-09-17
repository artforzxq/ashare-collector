"""腾讯行情适配器（免费、免账号、覆盖沪深京）。

为什么加它：东财的日线接口在部分网络下连接直接被断，baostock 走非标准端口 10030
且对匿名登录做 IP 限流（登录频繁会被拉黑）。腾讯这两个接口都走 443、支持前复权、
实时报价还能一次问多只，是目前最稳的免费日线来源。

两个必须知道的口径差异：
  1. 日线返回顺序是 (日期, 开盘, 收盘, 最高, 最低, 成交量)，**不是常见的 OHLC**；
  2. 成交量单位是"手"，这里乘 100 换成股；接口不给成交额，用 成交量 × 均价 估算，
     所以 amount 是估算值（做相对比较够用，当绝对金额看会失真）。
"""

from __future__ import annotations

import time
from datetime import datetime

from .base import BaseSource, DataSourceError
from . import split_code

DAILY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
QUOTE_URL = "https://qt.gtimg.cn/q="
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _symbol(code: str) -> str:
    """SH000300 → sh000300（腾讯用小写交易所前缀）。"""
    exchange, symbol = split_code(code)
    return f"{exchange.lower()}{symbol}"


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TencentSource(BaseSource):
    name = "tencent"
    capabilities = {"daily_bars", "intraday_snapshot", "trade_calendar"}

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self._last_request = 0.0

    def _throttle(self) -> None:
        """守项目里"免费源请求间隔 ≥0.5 秒"的规矩（config: sources.min_interval_sec）。"""
        gap = float(((self.cfg or {}).get("sources") or {}).get("min_interval_sec", 0.5) or 0)
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _requests(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - 依赖缺失分支
            raise DataSourceError("未安装 requests：pip install requests") from exc
        return requests

    def is_available(self) -> tuple[bool, str]:
        try:
            self._requests()
        except DataSourceError as exc:
            return False, str(exc)
        return True, "已安装"

    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        requests = self._requests()
        symbol = _symbol(code)
        try:
            span = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
        except ValueError:
            span = 400
        count = max(60, int(span * 0.75) + 20)
        url = f"{DAILY_URL}?param={symbol},day,{start},{end},{count},qfq"
        try:
            self._throttle()
            response = requests.get(url, headers=HEADERS, timeout=20)
            payload = response.json()
        except Exception as exc:
            raise DataSourceError(f"腾讯日线请求失败：{exc}") from exc

        block = (payload.get("data") or {}).get(symbol) or {}
        # 指数没有复权概念，键是 day；个股/ETF 是 qfqday
        series = block.get("qfqday") or block.get("day") or []
        if not series:
            return []

        rows: list[dict] = []
        previous_close = _to_float(block.get("prec"))
        for item in series:
            if len(item) < 6:
                continue
            trade_date = str(item[0])[:10]
            open_price, close = _to_float(item[1]), _to_float(item[2])
            high, low = _to_float(item[3]), _to_float(item[4])
            lots = _to_float(item[5])
            volume = lots * 100 if lots is not None else None
            if close is None:
                continue
            average = ((high or close) + (low or close) + close) / 3
            # 指数没有"股价"，成交量 × 点位算出来的不是金额——指数的 amount 留空，
            # 特征层会退回用成交量算量能。个股和 ETF 才做估算。
            amount = (
                round(volume * average, 2)
                if (volume and kind != "index")
                else None
            )
            pct_chg = (
                round((close / previous_close - 1) * 100, 4)
                if previous_close
                else None
            )
            rows.append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "pre_close": previous_close,
                    "volume": volume,
                    # 腾讯不返回成交额，用成交量 × 均价估算（指数除外，见上）
                    "amount": amount,
                    "pct_chg": pct_chg,
                    "adj_factor": 1.0,
                    "close_adj": close,      # 取的就是前复权价
                    "source": self.name,
                }
            )
            previous_close = close
        return [row for row in rows if start <= row["trade_date"] <= end]

    def intraday_snapshot(self, codes) -> list[dict]:
        """一次请求拿多只最新价（腾讯的 q= 接口支持逗号拼接）。"""
        requests = self._requests()
        symbols = [_symbol(code) for code in codes]
        if not symbols:
            return []
        try:
            self._throttle()
            response = requests.get(QUOTE_URL + ",".join(symbols), headers=HEADERS, timeout=15)
            response.encoding = "gbk"
            text = response.text
        except Exception as exc:
            raise DataSourceError(f"腾讯行情请求失败：{exc}") from exc

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows: list[dict] = []
        for line in text.strip().splitlines():
            if '="' not in line:
                continue
            head, _, body = line.partition('="')
            symbol = head.split("_")[-1].strip()
            fields = body.split("~")
            if len(fields) < 6:
                continue
            price = _to_float(fields[3])
            code = f"{symbol[:2].upper()}{symbol[2:]}"
            if price:
                rows.append({"code": code, "close": price, "dt": stamp})
        return rows

    def trade_calendar(self, start: str, end: str) -> list[dict]:
        """交易日历：指数有行情的日子就是交易日。

        用沪深300的日线反推，比去问第三方日历更直接，也不会多依赖一个接口。
        """
        rows = self.daily_bars("SH000300", start, end, "index")
        return [{"trade_date": row["trade_date"], "is_trading_day": 1} for row in rows]
