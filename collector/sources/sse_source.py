"""上海证券交易所适配器：ETF 份额（不经过东财，直接问交易所）。

为什么要单独写一个：ETF 份额原本只有 akshare 提供，而 akshare 的日线走东财
`push2his.eastmoney.com`——在这条网络上是**域名级被切**的（DNS、TCP、TLS 全通，
握手之后对端直接关连接，换 UA 也没用）。交易所自己的接口不需要账号、数据是一手的，
而且是正式披露口径，比"新浪最近总份额"那种估算值干净。

两个口径细节，都写在这里免得以后忘记：
  1. 份额是 **T+1 披露**的：今天查今天往往没有，所以要往前找最近一期，
     找到的如果不是当天，`is_estimated=1` 标出来，不假装是当天的数；
  2. 交易所给的 `TOT_VOL` 单位是**万份**，落库的 `shares` 要换算成**份**
     （实测：510300 是 2365968.77 万份 ≈ 236 亿份，和它的规模对得上）。
     这点和 akshare 的实现不一样——它直接把万份当份存了。

覆盖范围：只有沪市（510/511/512/513/515/516/517/518/520/560/561/562/563/588/589…）。
深市 ETF 份额在深交所，得另外接一个源（www.szse.cn）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .base import BaseSource, DataSourceError
from . import split_code

QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
# 交易所的接口要带 Referer，否则会被当成爬虫挡掉
HEADERS = {
    "Referer": "https://www.sse.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"   # ETF 产品列表·基金规模
LOOKBACK_DAYS = 12        # 份额 T+1 披露，加上节假日，最多往前找这么多天
SHARES_UNIT = 10_000      # 接口给的是万份 → 份


def _to_float(value):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_rows(payload: dict, trade_date: str, wanted: dict[str, str]) -> list[dict]:
    """交易所返回 → 落库用的行。

    `wanted` 是 {6 位代码: 带前缀代码}，只有观察池里的 ETF 才会落库。
    """
    rows: list[dict] = []
    for item in payload.get("result") or []:
        symbol = str(item.get("SEC_CODE") or "").strip()
        code = wanted.get(symbol)
        if not code:
            continue
        shares = _to_float(item.get("TOT_VOL"))
        if shares is None:
            continue
        stat_date = str(item.get("STAT_DATE") or "").strip()
        rows.append({
            "code": code,
            "trade_date": trade_date,
            "shares": round(shares * SHARES_UNIT, 2),
            # 交易所这个接口不给净值，折溢价与规模交给上层（它手里有当天收盘价）
            "nav": None,
            "close": None,
            "premium_rate": None,
            "assets": None,
            "is_estimated": 0 if stat_date == trade_date else 1,
            "stat_date": stat_date,
        })
    return rows


class SseSource(BaseSource):
    name = "sse"
    capabilities = {"etf_shares"}

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self._last_request = 0.0

    def _throttle(self) -> None:
        import time

        gap = float(((self.cfg or {}).get("sources") or {}).get("min_interval_sec", 0.5) or 0)
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def is_available(self) -> tuple[bool, str]:
        return True, "已安装"          # 只用标准库，没有依赖要检查

    def _query(self, stat_date: str, page_size: int = 2000) -> dict:
        """问一次交易所。stat_date 形如 2026-09-18。"""
        params = {
            "isPagination": "true",
            "pageHelp.pageSize": str(page_size),
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": SQL_ID,
            "STAT_DATE": stat_date,
        }
        url = QUERY_URL + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers=HEADERS)
        self._throttle()
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise DataSourceError(f"上交所返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DataSourceError(f"连不上上交所：{exc}") from exc
        except ValueError as exc:
            raise DataSourceError(f"上交所返回的不是 JSON：{exc}") from exc

    def etf_shares(self, codes, trade_date: str) -> list[dict]:
        """沪市 ETF 的份额。深市（15xxxx）不归上交所管，直接跳过。"""
        wanted: dict[str, str] = {}
        for code in codes or []:
            exchange, symbol = split_code(code)
            if exchange == "SH":
                wanted[symbol] = code
        if not wanted:
            return []

        errors: list[str] = []
        today = trade_date
        for step in range(LOOKBACK_DAYS):
            day = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=step)).strftime("%Y-%m-%d")
            try:
                payload = self._query(day)
            except DataSourceError as exc:
                errors.append(f"{day}：{exc}")
                continue
            rows = parse_rows(payload, today, wanted)
            if rows:
                return rows
        raise DataSourceError("上交所没给到这些 ETF 的份额：" + ("；".join(errors[:2]) or "最近 12 天都没有数据"))
