"""新浪行情适配器：补北交所这类腾讯只有当日数据的地方。

定位是**最后兜底**，原因有两个：
  1. 这个接口**不复权**（没有复权参数），而腾讯/baostock 给的是前复权；
  2. 它的历史深度受 datalen 限制（上限 1023 根）。

所以它不进主备源那对（那两个要互相交叉校验），只在全市场同步里当补充：
同一个标的的数据要么全来自腾讯、要么全来自新浪，不会在一段序列里混两种口径。

成交量单位是**股**（实测与腾讯的"手 ×100"一致），不需要再换算。
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from .base import BaseSource, DataSourceError, classify_symbol, exchange_of, normalize_symbol
from . import split_code

DAILY_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
# 全市场股票列表：node=hs_a 是全部 A 股（实测 5564 只，**含北交所**，是这里唯一能拿到北交所的源）
LIST_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
COUNT_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount"
# 个股资金流：日度主力/超大单净额。东财 push2his 在这条线路上被切时靠它兜底
FUND_FLOW_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                 "MoneyFlow.ssl_qsfx_zjlrqs")
PAGE_SIZE = 100          # 一页最多给 100 条，再大也不会多返回
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
MAX_BARS = 1023          # 接口单次上限


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _directory_rows(items) -> list[dict]:
    """新浪的行 → 清单（顺手去重、标类型）。"""
    rows: list[dict] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("symbol") or item.get("code")
        symbol = normalize_symbol(raw)
        kind = classify_symbol(raw)
        if kind == "other" or symbol in seen:
            continue
        seen.add(symbol)
        rows.append({"code": exchange_of(raw) + symbol, "name": str(item.get("name") or "").strip(), "type": kind})
    return rows


class SinaSource(BaseSource):
    name = "sina"
    capabilities = {"daily_bars", "symbol_directory", "fund_flow"}

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self._last_request = 0.0

    def _requests(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise DataSourceError("未安装 requests：pip install requests") from exc
        return requests

    def _throttle(self) -> None:
        gap = float(((self.cfg or {}).get("sources") or {}).get("min_interval_sec", 0.5) or 0)
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    # ---------------- 个股资金流（东财被封时的独立备胎） ----------------

    def fund_flow(self, code: str, days: int = 60) -> list[dict]:
        """个股日度资金流：主力净额 + 超大单净额（新浪）。

        为什么需要它：我们的资金流**原来只有东财 push2his 一个源**，而那条线路
        在这台机器上是域名级被切的（握手后直接断）——等于这个能力"有接口、没数据"。
        新浪这条走 `vip.stock.finance.sina.com.cn`，是完全不同的域名和风控面。

        口径（实测 2026-10-09，sh600519）：
          netamount   主力净额（元）
          r0_net      超大单净额（元）
          ratioamount 主力净占比（小数，×100 才和东财的"占比%"对齐）
          trade       收盘价　changeratio 涨跌幅（小数，×100 成 %）

        它**只有两档**（主力 + 超大单），没有大/中/小单——那三列留空，
        不拿 0 冒充"没有净流入"。
        """
        exchange, symbol = split_code(code)
        if not symbol:
            return []
        # 北交所要 bj 前缀：920xxx 写成 sh/sz 时新浪返回空数组（实测）
        prefix = "bj" if exchange == "BJ" else exchange.lower()
        self._throttle()
        try:
            response = self._requests().get(
                FUND_FLOW_URL,
                params={"page": 1, "num": max(1, min(int(days), 200)), "sort": "opendate",
                        "asc": 0, "daima": prefix + symbol},
                headers={**HEADERS, "Referer": "https://finance.sina.com.cn/"},
                timeout=20,
            )
        except Exception as exc:                       # 网络类异常统一成 DataSourceError
            raise DataSourceError(f"连不上新浪资金流：{exc}") from exc
        if response.status_code != 200:
            raise DataSourceError(f"新浪资金流返回 HTTP {response.status_code}")
        text = response.text
        if "[" not in text:
            return []
        try:
            payload = json.loads(text[text.index("["):text.rindex("]") + 1])
        except ValueError as exc:
            raise DataSourceError(f"新浪资金流返回的不是 JSON：{exc}") from exc

        rows: list[dict] = []
        for item in payload:
            day = str(item.get("opendate") or "")[:10]
            if not day:
                continue
            ratio = _to_float(item.get("ratioamount"))
            change = _to_float(item.get("changeratio"))
            rows.append({
                "code": code,
                "trade_date": day,
                "close": _to_float(item.get("trade")),
                "pct_chg": round(change * 100, 4) if change is not None else None,
                "main_net": _to_float(item.get("netamount")),
                "main_ratio": round(ratio * 100, 4) if ratio is not None else None,
                "super_net": _to_float(item.get("r0_net")),
                "large_net": None,          # 新浪这条只给主力与超大单两档
                "medium_net": None,
                "small_net": None,
                "source": self.name,
            })
        return rows

    def is_available(self) -> tuple[bool, str]:
        try:
            self._requests()
        except DataSourceError as exc:
            return False, str(exc)
        return True, "已安装"

    def symbol_directory(self) -> list[dict]:
        """全市场 A 股代码与名称，含北交所。

        这个列表接口一页最多 100 条，翻完 5500+ 只要 56 个请求，中间按 min_interval_sec
        限速。这是"偶尔跑一次"的操作，慢一点没关系，被限流才麻烦。
        """
        requests = self._requests()
        total = self._directory_total(requests)
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 80
        items: list[dict] = []
        for page in range(1, pages + 1):
            self._throttle()
            try:
                response = requests.get(
                    LIST_URL,
                    params={"page": page, "num": PAGE_SIZE, "sort": "symbol", "asc": 1, "node": "hs_a"},
                    headers=HEADERS,
                    timeout=20,
                )
                batch = response.json()
            except Exception as exc:
                raise DataSourceError(f"新浪代码表请求失败（第 {page} 页）：{exc}") from exc
            if not batch:
                break
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return _directory_rows(items)

    def _directory_total(self, requests) -> int:
        """先问总数，好知道要翻几页；问不到就按 80 页（约 8000 只）估。"""
        try:
            self._throttle()
            response = requests.get(COUNT_URL, params={"node": "hs_a"}, headers=HEADERS, timeout=20)
            return int(str(response.text).strip().strip('"'))
        except Exception:
            return 0

    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        requests = self._requests()
        exchange, symbol = split_code(code)
        try:
            span = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
        except ValueError:
            span = 400
        length = max(30, min(MAX_BARS, int(span * 0.75) + 20))
        url = f"{DAILY_URL}?symbol={exchange.lower()}{symbol}&scale=240&ma=no&datalen={length}"
        try:
            self._throttle()
            response = requests.get(url, headers=HEADERS, timeout=20)
            items = response.json()
        except Exception as exc:
            raise DataSourceError(f"新浪日线请求失败：{exc}") from exc
        if not items:
            return []

        rows: list[dict] = []
        previous_close = None
        for item in items:
            trade_date = str(item.get("day"))[:10]
            close = _to_float(item.get("close"))
            open_price = _to_float(item.get("open"))
            high, low = _to_float(item.get("high")), _to_float(item.get("low"))
            volume = _to_float(item.get("volume"))       # 单位：股
            if close is None:
                continue
            average = ((high or close) + (low or close) + close) / 3
            # 同腾讯：指数不做金额估算（点位 × 成交量不是金额）
            amount = round(volume * average, 2) if (volume and kind != "index") else None
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
                    "amount": amount,
                    "pct_chg": round((close / previous_close - 1) * 100, 4) if previous_close else None,
                    "adj_factor": 1.0,
                    "close_adj": close,                     # 接口不复权，原样存
                    "source": self.name,
                }
            )
            previous_close = close
        return [row for row in rows if start <= row["trade_date"] <= end]
