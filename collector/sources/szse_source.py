"""深圳证券交易所适配器：深市 ETF 的份额与净值（纯 JSON，只用标准库）。

**为什么不用 xlsx**：深交所那个报表接口提供两种模式，akshare 走的是 `SHOWTYPE=xlsx`
（导出整张表）。实测它对我们没用——导出回来是一列 基金代码 的竖排布局，解析器要跟着
它的排版走；而 JSON 模式支持**按代码筛选**（`txtkey1=<代码>`），一只票一个请求就够，
不用翻 53 页，也不用为了读 Excel 引入 openpyxl。所以这里只用 JSON。

两件事说清楚：
  1. **份额**：列名写着「当前规模（万份）」——单位是**万份**，落库要乘 10000 换算成份。
     交易所自己标注："该字段 T 日晚间更新的 T 日规模仅供参考，以 T+1 日早间为准"，
     所以落库打 `is_estimated=1`，和上交所那种按日披露的正式数字区分开。
  2. **净值**：另有按代码查的子接口，返回最近十个交易日的份额净值；净值通常是 T-1 的，
     拿到哪天就记哪天（`nav_date`），不假装是当天的。

覆盖范围：只深市（15xxxx/16xxxx）。沪市走上交所（sse_source）。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import BaseSource, DataSourceError
from . import split_code

REPORT_URL = "https://fund.szse.cn/api/report/ShowReport/data"
HEADERS = {
    "Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
SHARES_UNIT = 10_000                     # 接口给的是万份 → 份
_NUMBER = re.compile(r">([\d,\.]+)<")    # 交易所把数字包在 <a> 里返回


def number_from_html(value) -> float | None:
    """交易所的表格里数字是包在 HTML 链接里的：`<a ...>632,901.66</a>`。"""
    if value is None:
        return None
    text = str(value)
    match = _NUMBER.search(text)
    if match:
        text = match.group(1)
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def parse_share_row(item: dict, code: str, trade_date: str) -> dict | None:
    """基金列表里的一行 → 份额那一半。"""
    kind = str(item.get("jjlb") or "").strip()
    if kind and kind != "ETF":
        return None                      # 只认 ETF，别把 LOF/封闭式混进来
    shares = number_from_html(item.get("dqgm"))
    if shares is None:
        return None
    return {
        "code": code,
        "trade_date": trade_date,
        "shares": round(shares * SHARES_UNIT, 2),
        "nav": None,
        "close": None,
        "premium_rate": None,
        "assets": None,
        "is_estimated": 1,               # T 日晚间的规模，交易所说仅供参考
    }


def parse_nav(payload: dict) -> tuple[str | None, float | None]:
    """净值子接口 → (净值日期, 份额净值)，取最近一条。"""
    for row in (payload or {}).get("data") or []:
        try:
            return str(row.get("nav_date") or ""), float(row.get("nav_per_share"))
        except (TypeError, ValueError):
            continue
    return None, None


class SzseSource(BaseSource):
    name = "szse"
    capabilities = {"etf_shares"}

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self._last_request = 0.0

    def _throttle(self) -> None:
        gap = float(((self.cfg or {}).get("sources") or {}).get("min_interval_sec", 0.5) or 0)
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def is_available(self) -> tuple[bool, str]:
        return True, "已安装"            # 只用标准库，没有依赖要检查

    def _get(self, **params) -> dict:
        url = REPORT_URL + "?" + urllib.parse.urlencode(
            {"SHOWTYPE": "JSON", "TABKEY": "tab1", **params}
        )
        request = urllib.request.Request(url, headers=HEADERS)
        self._throttle()
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise DataSourceError(f"深交所返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DataSourceError(f"连不上深交所：{exc}") from exc
        except ValueError as exc:
            raise DataSourceError(f"深交所返回的不是 JSON：{exc}") from exc
        if isinstance(data, list) and data:
            return data[0] or {}
        return {}

    def etf_shares(self, codes, trade_date: str) -> list[dict]:
        """深市 ETF 的份额 + 净值。沪市不归深交所管，直接跳过。"""
        wanted: dict[str, str] = {}
        for code in codes or []:
            exchange, symbol = split_code(code)
            if exchange == "SZ":
                wanted[symbol] = code
        if not wanted:
            return []

        rows: list[dict] = []
        errors: list[str] = []
        for symbol, code in wanted.items():
            try:
                listing = self._get(CATALOGID="1000_lf", PAGENO="1", PAGESIZE="20", txtkey1=symbol)
            except DataSourceError as exc:
                errors.append(f"{code} 份额：{exc}")
                continue
            item = next((row for row in (listing.get("data") or [])
                         if symbol in str(row.get("sys_key") or "")), None)
            row = parse_share_row(item, code, trade_date) if item else None
            if row is None:
                errors.append(f"{code} 份额：列表里没找到（或不是 ETF）")
                continue
            try:
                nav_date, nav = parse_nav(self._get(CATALOGID="fund_jjjz", txtDm=symbol))
            except DataSourceError as exc:
                nav_date, nav = None, None
                errors.append(f"{code} 净值：{exc}")
            if nav:
                row["nav"] = nav
                row["assets"] = round(row["shares"] * nav, 2)
                if nav_date and nav_date != trade_date:
                    row["is_estimated"] = 1
            rows.append(row)

        if not rows:
            raise DataSourceError("深交所没取到份额：" + ("；".join(errors[:2]) or "未知原因"))
        return rows
