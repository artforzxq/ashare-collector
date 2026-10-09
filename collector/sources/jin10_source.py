"""金十数据适配器：沪深两市融资融券的独立备胎。

为什么需要它：融资融券原来只有两条路——上交所/深交所官网。
深交所那条能用（见 `szse_source.margin`），**上交所那条在本机网络上稳定返回 HTTP 500**
（`queryMargin.do`，换四种参数写法都一样），于是沪市两融一直是空的。

金十这两份数据是**静态 JSON**（`cdn.jin10.com`，和交易所完全不同的域名与风控面）：
  · `fs_1.json` = 沪市　· `fs_2.json` = 深市
  每份 300 KB 左右、**一次请求拿全部历史**（实测 2010-03-31 起 4000 多个交易日）。

两个口径上的好处：
  1. 它的元数据里**直接写了单位**（`keys: [{name: 融资余额, unit: 元}]`），不用猜；
  2. 深市那份和我们从深交所官网取到的数字**完全对得上**（2026-10-08 融资余额
     两边都是 1.238 万亿），等于给深市多了一个交叉校验源。

所以它是"补缺口 + 当对照"，不抢主源的位置——任务层按"先到先得"合并，
官方源先写进去，金十只补齐官方取不到的那部分（沪市）。

自检：写库前验一次恒等式「融资余额 + 融券余额 ≈ 融资融券余额」。
单位哪天被上游改掉（比如从元变成亿元），这条会立刻炸出来，而不是静默写进库。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from .base import BaseSource, DataSourceError

BASE_URL = "https://cdn.jin10.com/data_center/reports/fs_{index}.json"
MARKETS = {"SH": 1, "SZ": 2}
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Referer": "https://datacenter.jin10.com/",
}
# 列顺序是它自己定的：融资买入额 / 融资余额 / 融券卖出量 / 融券余量 / 融券余额 / 融资融券余额
COLUMNS = ("financing_buy", "financing_balance", "short_sell_volume",
           "short_volume", "securities_lending", "total")
LOOKBACK_DAYS = 10          # 披露是 T+1，当天没有就往回找


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Jin10Source(BaseSource):
    name = "jin10"
    capabilities = {"margin"}

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self._last_request = 0.0
        self._cache: dict[str, dict] = {}

    def _throttle(self) -> None:
        gap = float(((self.cfg or {}).get("sources") or {}).get("min_interval_sec", 0.5) or 0)
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def is_available(self) -> tuple[bool, str]:
        return True, "已安装"            # 只用标准库

    def _series(self, exchange: str) -> dict:
        """{"2026-10-08": {...}}。同一个进程里只取一次（两份文件都不大，缓存在内存里）。"""
        if exchange in self._cache:
            return self._cache[exchange]
        self._throttle()
        url = BASE_URL.format(index=MARKETS[exchange])
        request = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise DataSourceError(f"金十返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DataSourceError(f"连不上金十：{exc}") from exc
        except ValueError as exc:
            raise DataSourceError(f"金十返回的不是 JSON：{exc}") from exc

        units = {item.get("name"): item.get("unit") for item in payload.get("keys") or []}
        if units.get("融资余额") != "元":
            raise DataSourceError(f"金十的单位变了（融资余额={units.get('融资余额')}），先对口径再写库")
        series: dict[str, dict] = {}
        for day, values in (payload.get("values") or {}).items():
            if not isinstance(values, list) or len(values) != len(COLUMNS):
                continue
            series[str(day)[:10]] = dict(zip(COLUMNS, (_number(v) for v in values)))
        self._cache[exchange] = series
        return series

    def margin(self, trade_date: str) -> list[dict]:
        """沪深两市的融资融券汇总（一次请求一天，两市各一行）。"""
        fetched = datetime.now().strftime("%Y-%m-%d")
        rows: list[dict] = []
        for exchange in ("SH", "SZ"):
            series = self._series(exchange)
            for offset in range(LOOKBACK_DAYS):
                day = (datetime.strptime(trade_date, "%Y-%m-%d")
                       - timedelta(days=offset)).strftime("%Y-%m-%d")
                item = series.get(day)
                if not item:
                    continue
                balance, lending = item["financing_balance"], item["securities_lending"]
                total = item["total"]
                if balance and lending and total and abs(balance + lending - total) > total * 0.01:
                    raise DataSourceError(
                        f"金十 {exchange} {day} 的恒等式不成立："
                        f"{balance} + {lending} != {total}（单位可能变了）")
                rows.append({
                    "trade_date": trade_date,
                    "market": exchange,
                    "data_date": day,
                    "fetched_date": fetched,
                    "financing_balance": balance,
                    "securities_lending": lending,
                    "total": total,
                    # 与 akshare 那条口径一致：这个字段存的是融资买入额
                    "net_buy": item["financing_buy"],
                    "source": self.name,
                })
                break
        return rows
