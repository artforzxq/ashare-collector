"""腾讯行情适配器（免费、免账号、覆盖沪深京）。

为什么加它：东财的日线接口在部分网络下连接直接被断，baostock 走非标准端口 10030
且对匿名登录做 IP 限流（登录频繁会被拉黑）。腾讯这两个接口都走 443、支持前复权、
实时报价还能一次问多只，是目前最稳的免费日线来源。

三个必须知道的口径差异：
  1. 日线返回顺序是 (日期, 开盘, 收盘, 最高, 最低, 成交量)，**不是常见的 OHLC**；
  2. 成交量单位是"手"，这里乘 100 换成股；
  3. **日线接口不返回成交额**（实测三种端点都只有 6 个字段），所以日线的 amount
     是"成交量 × 均价"的估算值——做相对比较够用，当绝对金额看会失真。
     （分时和实时报价接口是给真成交额的，见 intraday_bars / intraday_snapshot。）
"""

from __future__ import annotations

import time
from datetime import datetime

from .base import BaseSource, DataSourceError
from . import split_code

DAILY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
INTRADAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
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


def _parse_intraday(payload: dict, symbol: str, code: str) -> list[dict]:
    """把 minute/query 的返回解析成分钟线。

    每行形如 "0930 1257.98 140 17611720.00"，四个字段分别是
    **时间、价格、累计成交量（手）、累计成交额（元）**——注意后两个是**当天累计**，
    不是这一分钟的量（实测：最后一行累计成交额 12.39 亿，正好等于当天成交额）。
    所以这里要逐行做差，还原出每分钟的成交量和成交额。

    成交额是接口给的真值，不用估算；只有接口没给成交额时才退回 价 × 量。
    """
    node = (payload.get("data") or {}).get(symbol) or {}
    block = node.get("data") if isinstance(node.get("data"), dict) else node
    block = block or {}
    lines = block.get("data") or []
    stamp_date = str(block.get("date") or "")
    if len(stamp_date) == 8 and stamp_date.isdigit():
        day = f"{stamp_date[:4]}-{stamp_date[4:6]}-{stamp_date[6:]}"
    else:
        day = datetime.now().strftime("%Y-%m-%d")

    rows: list[dict] = []
    prev_volume = prev_amount = 0.0
    for line in lines:
        parts = str(line).split()
        if len(parts) < 2:
            continue
        clock, price = parts[0], _to_float(parts[1])
        if not price or len(clock) < 4:
            continue
        cum_volume = _to_float(parts[2]) or 0.0              # 累计成交量（手）
        cum_amount = _to_float(parts[3]) if len(parts) > 3 else None
        volume = max(0.0, cum_volume - prev_volume) * 100    # 手 → 股，差分出这一分钟
        if cum_amount is None:
            amount = round(price * volume, 2)                # 接口没给才估算
        else:
            amount = round(max(0.0, cum_amount - prev_amount), 2)
            prev_amount = cum_amount
        prev_volume = cum_volume
        rows.append({
            "code": code,
            "dt": f"{day} {clock[:2]}:{clock[2:4]}",
            "period": 1,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
            "amount": amount,
            "source": "tencent",
        })
    return rows


class TencentSource(BaseSource):
    name = "tencent"
    capabilities = {"daily_bars", "intraday_snapshot", "intraday_bars", "trade_calendar"}

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
        """一次请求拿多只最新价（腾讯的 q= 接口支持逗号拼接）。

        顺带把报价里本来就有的字段也带出来：涨跌幅、最高最低、成交量、**成交额**。
        成交额在这里是真值（万元），不用像日线那样估算。
        """
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
            if not price:
                continue
            when = str(fields[30]) if len(fields) > 30 and fields[30] else ""
            rows.append({
                "code": code,
                "name": fields[1] if len(fields) > 1 else "",
                "close": price,
                "pre_close": _to_float(fields[4]) if len(fields) > 4 else None,
                "open": _to_float(fields[5]) if len(fields) > 5 else None,
                "high": _to_float(fields[33]) if len(fields) > 33 else None,
                "low": _to_float(fields[34]) if len(fields) > 34 else None,
                "pct_chg": _to_float(fields[32]) if len(fields) > 32 else None,
                "volume": (_to_float(fields[36]) or 0.0) * 100 if len(fields) > 36 else None,
                "amount": (_to_float(fields[37]) or 0.0) * 1e4 if len(fields) > 37 else None,
                "quote_time": (f"{when[:4]}-{when[4:6]}-{when[6:8]} {when[8:10]}:{when[10:12]}:{when[12:14]}"
                               if len(when) >= 14 else None),
                "dt": stamp,
            })
        return rows

    def intraday_bars(self, code: str, period: int = 1) -> list[dict]:
        """当日分时线：一分钟一个点（09:30 起，含成交量和均价）。

        这个接口只给**当天**，历史分钟线要另外付费/另找源，所以盘中多跑几次就多攒几天。
        价格是未复权的成交价——同一天之内不涉及复权，和日线的前复权口径不冲突。
        成交量单位是"手"，这里乘 100 换成股；接口不给成交额，按 价 × 量 估算，
        和日线那条"腾讯不返回成交额"的处理保持一致。
        """
        if period != 1:
            raise DataSourceError(f"腾讯分时只有 1 分钟粒度，不支持 period={period}")
        symbol = _symbol(code)
        requests = self._requests()
        try:
            self._throttle()
            response = requests.get(INTRADAY_URL, params={"code": symbol},
                                    headers=HEADERS, timeout=15)
            payload = response.json()
        except Exception as exc:
            raise DataSourceError(f"腾讯分时请求失败：{exc}") from exc

        return _parse_intraday(payload, symbol, code)

    def trade_calendar(self, start: str, end: str) -> list[dict]:
        """交易日历：指数有行情的日子就是交易日。

        用沪深300的日线反推，比去问第三方日历更直接，也不会多依赖一个接口。
        """
        rows = self.daily_bars("SH000300", start, end, "index")
        return [{"trade_date": row["trade_date"], "is_trading_day": 1} for row in rows]
