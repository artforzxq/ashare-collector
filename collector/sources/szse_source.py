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
# 两融与龙虎榜在 www 域（不是 fund 域），报表接口的写法一样，前缀不同
WWW_REPORT_URL = "https://www.szse.cn/api/report/ShowReport/data"
MARGIN_REFERER = "https://www.szse.cn/disclosure/margin/object/index.html"
LHB_REFERER = "https://www.szse.cn/disclosure/supervision/dealinfo/index.html"
# 融资融券披露是 T+1：今天查不到就往前找这么多个自然日
MARGIN_LOOKBACK_DAYS = 10
# 龙虎榜明细要逐只问（一次请求给买卖五席位），一天几十只；超过这个数就只写汇总
LHB_DETAIL_LIMIT = 80
WWW_HEADERS = {
    "Referer": MARGIN_REFERER,
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
YI = 100_000_000          # 深交所汇总表的金额单位是**亿元**（实测深市融资余额 12,358.98 亿）
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


def _number(value):
    """深交所的数字带千分位，也可能带单位文字（如 '5,158,052,404 元'）。"""
    text = str(value or "").strip().split(" ")[0].replace(",", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _scale(value) -> float | None:
    """汇总表的金额单位是亿元 → 元。"""
    number = _number(value)
    return round(number * YI, 2) if number is not None else None


def _amount(value) -> float | None:
    """汇总表里的成交金额同样是亿元。"""
    return _scale(value)


def _clean(text) -> str:
    """深交所的名称里带 &nbsp; 和全角空格。"""
    import html

    return re.sub(r"\s+", "", html.unescape(str(text or ""))).replace("\u3000", "")


class SzseSource(BaseSource):
    name = "szse"
    capabilities = {"etf_shares", "margin", "lhb"}

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

    def _www_get(self, params: dict, referer: str) -> list:
        """www.szse.cn 的报表接口：返回**整段**（不取 data[0]），因为有些报表是多段。"""
        url = WWW_REPORT_URL + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={**WWW_HEADERS, "Referer": referer})
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
        return data if isinstance(data, list) else []

    # ---------------- 融资融券（交易所官方，沪深分开披露） ----------------

    def margin(self, trade_date: str) -> list[dict]:
        """深市融资融券**汇总**（一个交易日一行）。

        用的是报表的 tab1「融资融券交易总量」，**不带 TABKEY** 时第一段就是它，
        一个请求拿一天（实测深市 2026-09-30：融资余额 12,358.98 亿）。
        注意 tab1 与 tab2 的单位不一样：汇总表全是**亿元**，明细表里融券余额是万元——
        我们只取汇总表，所以统一乘 1e8。

        披露是 T+1：当天问不到就往前找（最多 MARGIN_LOOKBACK_DAYS 天），
        找到哪天就在 data_date 里记哪天——不拿旧数据冒充当天。
        """
        from datetime import datetime, timedelta

        fetched = datetime.now().strftime("%Y-%m-%d")
        for offset in range(MARGIN_LOOKBACK_DAYS):
            day = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=offset)).strftime("%Y-%m-%d")
            parts = self._www_get(
                {"SHOWTYPE": "JSON", "CATALOGID": "1837_xxpl", "txtDate": day, "tab1PAGENO": 1},
                MARGIN_REFERER)
            summary = next((part for part in parts if (part.get("metadata") or {}).get("tabkey") == "tab1"), None)
            rows = (summary or {}).get("data") or []
            if not rows:
                continue
            item = rows[0]
            return [{
                "trade_date": trade_date,
                "market": "SZ",
                "data_date": day,
                "fetched_date": fetched,
                "financing_balance": _scale(item.get("jrrzye")),
                "securities_lending": _scale(item.get("jrrjye")),
                "total": _scale(item.get("jrrzrjye")),
                # 与 akshare 那条口径保持一致：这个字段存的是**融资买入额**
                "net_buy": _scale(item.get("jrrzmr")),
                "source": self.name,
            }]
        return []

    # ---------------- 龙虎榜（交易所官方，含营业部席位） ----------------

    def lhb(self, start: str, end: str) -> list[dict]:
        """深市龙虎榜：先用汇总表拿到上榜名单，再逐只问买卖席位算出净买额。

        为什么不只用汇总表：汇总表只有"谁上榜、因为什么、成交多少"，
        没有买卖金额——而龙虎榜的信息量恰恰在净买额上（机构/游资是买还是卖）。
        席位明细接口一次给买卖各五席位，一只票一个请求。
        超过 LHB_DETAIL_LIMIT 只时只写汇总（净买额留空），并在日志里说明。
        """
        from datetime import datetime, timedelta

        rows: list[dict] = []
        cursor = datetime.strptime(start, "%Y-%m-%d")
        last = datetime.strptime(end, "%Y-%m-%d")
        while cursor <= last:
            day = cursor.strftime("%Y-%m-%d")
            cursor += timedelta(days=1)
            listed = self._lhb_day(day)
            if not listed:
                continue
            detailed = 0
            for item in listed:
                symbol = str(item.get("zqdm") or "").strip()
                reason = _clean(item.get("plyy")) or "上榜"
                row = {
                    "trade_date": day,
                    "code": f"SZ{symbol}",
                    "name": _clean(item.get("zqjc")),
                    "reason": reason,
                    "close": None,
                    "pct_chg": None,
                    "net_buy": None,
                    "buy_amount": None,
                    "sell_amount": None,
                    "turnover": _amount(item.get("cjje")),      # 汇总表这里是**亿元**
                    "net_ratio": None,
                    "source": self.name,
                }
                if detailed < LHB_DETAIL_LIMIT:
                    seats = self._lhb_seats(day, symbol, item.get("bz"))
                    if seats:
                        buy = sum(v for kind, v in seats if kind == "买")
                        sell = sum(v for kind, v in seats if kind == "卖")
                        row.update({"buy_amount": buy, "sell_amount": sell, "net_buy": buy - sell})
                        detailed += 1
                rows.append(row)
        return rows

    def _lhb_day(self, day: str) -> list[dict]:
        """某一天的上榜名单（分页翻完）。"""
        items: list[dict] = []
        page = 1
        while page <= 20:                                   # 保险丝：一天最多翻 20 页
            parts = self._www_get(
                {"SHOWTYPE": "JSON", "CATALOGID": "1842_xxpl", "TABKEY": "tab1",
                 "txtStart": day, "txtEnd": day, "random": "0.9", "PAGENO": page},
                LHB_REFERER)
            part = (parts[0] if parts else {}) or {}
            rows = part.get("data") or []
            items.extend(rows)
            meta = part.get("metadata") or {}
            if page >= int(meta.get("pagecount") or 1) or not rows:
                break
            page += 1
        return items

    def _lhb_seats(self, day: str, symbol: str, link: str | None) -> list[tuple[str, float]]:
        """一只票当天买卖席位金额。失败就当没有（不因为一只票拖垮整天的龙虎榜）。"""
        zb = "0902"
        if link:
            found = re.search(r"ZBDM=(\d+)", str(link))
            if found:
                zb = found.group(1)
        try:
            parts = self._www_get(
                {"SHOWTYPE": "JSON", "CATALOGID": "1842_detal", "TABKEY": "tab1,tab2",
                 "DQRQ": day, "ZQDM": symbol, "ZBDM": zb},
                LHB_REFERER)
        except DataSourceError:
            return []
        pairs: list[tuple[str, float]] = []
        for part in parts:
            for item in (part.get("data") or []):
                label = str(item.get("mmlb") or "")
                if label.startswith("买"):
                    pairs.append(("买", _number(item.get("mrje")) or 0.0))
                elif label.startswith("卖"):
                    pairs.append(("卖", _number(item.get("mcje")) or 0.0))
        return pairs

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
