"""AkShare 适配器（免费，覆盖最广，适合当备份源）。

注意：AkShare 的接口名随上游网页调整而变化，启用前请用 `python -m collector.cli sources`
确认能力探测通过，并按当期文档核对函数签名。

本适配器除日线外，还提供上层需要的四种能力（全部免费、无需账号）：
  market_snapshot    全市场快照 → 市场广度（涨跌家数、涨停数、中位数）
  intraday_snapshot  指数 / ETF 实时价 → 盘中触碰支撑带判断
  etf_shares         沪深交易所披露的 ETF 份额与净值
  margin             沪深两市融资融券余额
  trade_calendar     交易日历

每个接口都是"尽力而为"：某一路径挂了只丢那部分，其余照常返回；
全挂才抛 DataSourceError，让上层照常记一条 failed 体检记录。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from .base import BaseSource, DataSourceError, canonicalize_frame, exchange_of
from . import split_code


# 东财行情中心的通用列表接口。字段编号取自 akshare 自己的实现：
#   stock_zh_index_spot_em 与 fund_etf_spot_em 的映射一致 ——
#   f2=最新价 f3=涨跌幅 f5=成交量 f6=成交额 f12=代码 f14=名称 f18=昨收
SPOT_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
SPOT_FIELDS = "f2,f3,f5,f6,f12,f14,f15,f16,f17,f18"
SPOT_UT = "bd1d9ddb04089700cf9c27f6f7426281"
SPOT_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"   # 沪深京 A 股


def _num(value) -> float | None:
    """把 '1,234.5'、'—'、'' 这类值转成 float；转不动就返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "—", "None", "nan", "NoneType"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _prefixed(symbol: str) -> str:
    """6 位数字代码 → 带上交易所前缀（与项目其它地方的口径一致）。"""
    text = str(symbol or "").strip().upper()
    if text[:2] in ("SH", "SZ", "BJ"):
        return text
    if text.startswith(("6", "9", "5")):
        return "SH" + text
    if text.startswith(("4", "8")):
        return "BJ" + text
    return "SZ" + text


def _symbol(value) -> str:
    """交易所返回的代码统一成 6 位数字，方便和观察池对上。"""
    text = str(value or "").strip()
    if "." in text:                     # 有些接口给的是 510300.SH
        text = text.split(".")[0]
    if text[:2].lower() in ("sh", "sz") and len(text) > 2:   # 新浪给的是 sh510300
        text = text[2:]
    text = text.lstrip("0") or "0"
    return text.zfill(6)


def _dash(value) -> str | None:
    """20260916 → 2026-09-16；已经是横线格式或取不到就原样返回。"""
    text = str(value or "").strip()[:10]
    if not text:
        return None
    if "-" in text:
        return text
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _recent_dates(trade_date: str, days: int) -> list[str]:
    """从 trade_date 往前逐日列出 YYYYMMDD。

    融资融券和 ETF 份额都是 T+1 披露：当天收盘后跑，往往还查不到当天的数，
    得往前找最近一个有数据的交易日，而不是直接判成"接口挂了"。
    """
    cursor = datetime.strptime(trade_date, "%Y-%m-%d")
    return [(cursor - timedelta(days=offset)).strftime("%Y%m%d") for offset in range(days)]


def _is_no_data(exc: Exception) -> bool:
    """akshare 在该日尚无披露时会抛这类异常，属于正常情况，不算故障。"""
    text = str(exc)
    return any(mark in text for mark in ("Length mismatch", "No tables found", "0 elements"))


def _brief(errors: list[str], limit: int = 2) -> str:
    """错误信息只留前几条：往前找十几天的日期时，原始堆栈能刷屏两千字。"""
    if not errors:
        return "接口未返回数据"
    head = "；".join(error[:100] for error in errors[:limit])
    if len(errors) > limit:
        head += f"（另有 {len(errors) - limit} 次同类失败）"
    return head


class AkshareSource(BaseSource):
    name = "akshare"
    capabilities = {
        "daily_bars",
        "market_snapshot",
        "intraday_snapshot",
        "etf_shares",
        "lhb",
        "fund_flow",
        "margin",
        "trade_calendar",
    }

    def _ak(self):
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - 依赖缺失分支
            raise DataSourceError("未安装 akshare：pip install akshare") from exc
        return ak

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self._last_request = 0.0

    def _throttle(self) -> None:
        """遵守配置里的免费源请求间隔下限（sources.min_interval_sec）。"""
        gap = float(((self.cfg or {}).get("sources") or {}).get("min_interval_sec", 0.5) or 0)
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def is_available(self) -> tuple[bool, str]:
        try:
            self._ak()
            return True, "已安装"
        except DataSourceError as exc:
            return False, str(exc)

    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        ak = self._ak()
        exchange, symbol = split_code(code)
        start_compact, end_compact = start.replace("-", ""), end.replace("-", "")

        if kind == "index":
            frame = ak.stock_zh_index_daily_em(symbol=f"{exchange.lower()}{symbol}")
            rows = [r for r in canonicalize_frame(frame) if start <= (r.get("trade_date") or "") <= end]
        elif kind == "etf":
            frame = ak.fund_etf_hist_em(
                symbol=symbol, period="daily", start_date=start_compact, end_date=end_compact, adjust="qfq"
            )
            rows = canonicalize_frame(frame)
        else:
            frame = ak.stock_zh_a_hist(
                symbol=symbol, period="daily", start_date=start_compact, end_date=end_compact, adjust="qfq"
            )
            rows = canonicalize_frame(frame)

        for row in rows:
            row["code"] = code
            row["source"] = self.name
        return rows

    def market_snapshot(self, trade_date: str | None = None, codes=None) -> list[dict]:
        """全市场快照（东财）。它自己能列出全市场，所以 codes 用不上，收下只是为了接口一致。"""
        """全市场快照。

        快路径：一次请求拿全市场（东财允许大 pageSize）。akshare 自带的实现要翻五十多页、
        每页还带重试和睡眠，网络一抖就能耗掉几分钟。网络不通直接抛错让上层记 failed，
        不再退到慢路径；只有"接口结构变了"才退回 akshare 的分页实现。
        """
        direct = self._snapshot_direct()
        if direct:
            return direct
        frame = self._ak().stock_zh_a_spot_em()
        rows = canonicalize_frame(frame)
        for row in rows:
            row["source"] = self.name
        return rows

    def _snapshot_direct(self) -> list[dict]:
        """东财 clist 快路径：一次请求拿全市场。"""
        try:
            import requests
        except ImportError:  # pragma: no cover - requests 随 akshare 一起装
            return []
        params = {
            "pn": "1",
            "pz": "6000",
            "po": "1",
            "np": "1",
            "ut": SPOT_UT,
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": SPOT_FS,
            "fields": SPOT_FIELDS,
        }
        try:
            self._throttle()
            response = requests.get(
                SPOT_URL, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"}
            )
            payload = response.json()
        except Exception as exc:
            raise DataSourceError(f"东财全市场快照不可达：{exc}") from exc

        items = (payload.get("data") or {}).get("diff") or []
        if isinstance(items, dict):        # 接口有时返回 {"0": {...}, "1": {...}}
            items = list(items.values())
        rows: list[dict] = []
        for item in items:
            symbol = _symbol(item.get("f12"))
            if not symbol:
                continue
            lots = _num(item.get("f5"))          # 东财成交量单位是"手"
            rows.append(
                {
                    "code": exchange_of(symbol) + symbol,
                    "name": item.get("f14"),
                    "close": _num(item.get("f2")),
                    "pct_chg": _num(item.get("f3")),
                    "volume": lots * 100 if lots is not None else None,   # 手 → 股，和 baostock 对齐
                    "amount": _num(item.get("f6")),
                    "high": _num(item.get("f15")),
                    "low": _num(item.get("f16")),
                    "open": _num(item.get("f17")),
                    "pre_close": _num(item.get("f18")),
                    "source": self.name,
                }
            )
        return rows

    # ---- 交易日历 ----

    def symbol_directory(self) -> list[dict]:
        """全市场 A 股的代码与名称，页面里按名字搜索用（一次几千行，缓存到库里）。"""
        ak = self._ak()
        self._throttle()
        frame = ak.stock_info_a_code_name()
        rows: list[dict] = []
        for row in frame.to_dict("records"):
            symbol = _symbol(row.get("code"))
            if not symbol:
                continue
            rows.append({"code": exchange_of(symbol) + symbol, "name": str(row.get("name") or "").strip()})
        return rows

    def trade_calendar(self, start: str, end: str) -> list[dict]:
        """新浪的历史交易日历（只有交易日，没有休市日）。"""
        frame = self._ak().tool_trade_date_hist_sina()
        days = sorted(str(day)[:10] for day in frame["trade_date"].tolist())
        return [
            {"trade_date": day, "is_trading_day": 1}
            for day in days
            if start <= day <= end
        ]

    # ---- 盘中快照 ----

    def intraday_snapshot(self, codes) -> list[dict]:
        """指数与 ETF 的最新价。codes 形如 ['SH000300', 'SH510300']。"""
        ak = self._ak()
        wanted: dict[str, str] = {}
        for code in codes:
            _, symbol = split_code(code)
            wanted[_symbol(symbol)] = code

        watch = (self.cfg or {}).get("watchlist", {})
        etf_only = {_symbol(split_code(c)[1]) for c in watch.get("etfs", [])}
        index_only = {_symbol(split_code(c)[1]) for c in watch.get("indices", [])}

        price: dict[str, float] = {}
        errors: list[str] = []

        # ETF：一次拿全市场
        if set(wanted) - index_only:
            try:
                frame = ak.fund_etf_spot_em()
                for row in frame.to_dict("records"):
                    symbol = _symbol(row.get("代码"))
                    if symbol in wanted and symbol not in price:
                        price[symbol] = _num(row.get("最新价"))
            except Exception as exc:
                errors.append(f"ETF 行情：{exc}")

        # 指数：按板块分组取，取齐了就停
        if set(wanted) - etf_only:
            for group in ("沪深重要指数", "中证系列指数", "上证系列指数", "深证系列指数"):
                if len(price) >= len(wanted):
                    break
                try:
                    frame = ak.stock_zh_index_spot_em(symbol=group)
                    for row in frame.to_dict("records"):
                        symbol = _symbol(row.get("代码"))
                        if symbol in wanted and symbol not in price:
                            price[symbol] = _num(row.get("最新价"))
                except Exception as exc:
                    errors.append(f"指数 {group}：{exc}")
            # 东财的指数接口在部分网络下会被直接断连，用新浪兜底
            if not (set(wanted) - etf_only) <= set(price):
                try:
                    frame = ak.stock_zh_index_spot_sina()
                    for row in frame.to_dict("records"):
                        symbol = _symbol(row.get("代码"))
                        if symbol in wanted and symbol not in price:
                            price[symbol] = _num(row.get("最新价"))
                except Exception as exc:
                    errors.append(f"新浪指数：{exc}")

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            {"code": wanted[symbol], "close": value, "dt": stamp}
            for symbol, value in price.items()
            if value
        ]
        if not rows:
            raise DataSourceError("akshare 未取到盘中快照：" + _brief(errors))
        return rows

    # ---- ETF 份额 ----

    @staticmethod
    def _pick(columns, *keywords) -> str | None:
        """按关键字找列名。上游改列名是常事（规格里专门提醒过），所以不硬编码。

        要求所有关键字都出现在列名里，命中多个则取最短的那个（更精确）。
        """
        hits = [c for c in columns if all(k in str(c) for k in keywords)]
        return sorted(hits, key=len)[0] if hits else None

    def lhb(self, start: str, end: str) -> list[dict]:
        """龙虎榜（东财）：一段日期内的全部上榜记录。

        同一只票同一天可能因为多条原因上榜，所以 reason 要留着——它往往是最有信息量的那部分
        （"日涨幅偏离值达 7%"和"机构专用席位买入"完全是两回事）。
        """
        ak = self._ak()
        try:
            df = ak.stock_lhb_detail_em(start_date=start.replace("-", ""), end_date=end.replace("-", ""))
        except Exception as exc:
            raise DataSourceError(f"龙虎榜请求失败：{exc}") from exc

        columns = list(df.columns)
        code_col = self._pick(columns, "代码")
        day_col = self._pick(columns, "上榜日")
        if not code_col or not day_col:
            raise DataSourceError(f"龙虎榜列名对不上：{columns}")
        name_col = self._pick(columns, "名称")
        reason_col = self._pick(columns, "原因")
        close_col = self._pick(columns, "收盘价")
        pct_col = self._pick(columns, "涨跌幅")
        net_col = self._pick(columns, "净买额")
        buy_col = self._pick(columns, "买入额")
        sell_col = self._pick(columns, "卖出额")
        amount_col = self._pick(columns, "成交额")
        ratio_col = self._pick(columns, "净买额", "占")

        rows: list[dict] = []
        for item in df.to_dict("records"):
            symbol = str(item.get(code_col) or "").strip()
            day = str(item.get(day_col) or "")[:10]
            if not symbol or not day:
                continue
            code = _prefixed(symbol)
            rows.append({
                "trade_date": day,
                "code": code,
                "name": str(item.get(name_col) or "") if name_col else "",
                "reason": str(item.get(reason_col) or "") if reason_col else "",
                "close": _num(item.get(close_col)) if close_col else None,
                "pct_chg": _num(item.get(pct_col)) if pct_col else None,
                "net_buy": _num(item.get(net_col)) if net_col else None,
                "buy_amount": _num(item.get(buy_col)) if buy_col else None,
                "sell_amount": _num(item.get(sell_col)) if sell_col else None,
                "turnover": _num(item.get(amount_col)) if amount_col else None,
                "net_ratio": _num(item.get(ratio_col)) if ratio_col else None,
                "source": self.name,
            })
        return rows

    def fund_flow(self, code: str) -> list[dict]:
        """个股资金流（东财，近约 100 个交易日）：主力/超大单/大单/中单/小单净流入。

        单位：元；占比单位：%。这是"资金"类因子唯一能拿到**历史序列**的免费源
        （实时北向那条 2024 年 8 月起就停止披露了，规格里也已排除）。
        """
        ak = self._ak()
        exchange, symbol = split_code(code)
        # 资金流走东财 push2his，这条在部分网络下很脆（实测连续请求会被断连），
        # 所以间隔放大到 2 秒，宁可慢也别把它打成黑名单。
        time.sleep(2.0)
        try:
            df = ak.stock_individual_fund_flow(stock=symbol, market=exchange.lower())
        except Exception as exc:
            raise DataSourceError(f"资金流请求失败：{exc}") from exc

        columns = list(df.columns)
        day_col = self._pick(columns, "日期")
        if not day_col:
            raise DataSourceError(f"资金流列名对不上：{columns}")
        keep = {
            "close": self._pick(columns, "收盘价"),
            "pct_chg": self._pick(columns, "涨跌幅"),
            "main_net": self._pick(columns, "主力", "净额"),
            "main_ratio": self._pick(columns, "主力", "占比"),
            "super_net": self._pick(columns, "超大单", "净额"),
            "large_net": self._pick(columns, "大单", "净额"),
            "medium_net": self._pick(columns, "中单", "净额"),
            "small_net": self._pick(columns, "小单", "净额"),
        }
        rows: list[dict] = []
        for item in df.to_dict("records"):
            day = str(item.get(day_col) or "")[:10]
            if not day:
                continue
            row = {"code": code, "trade_date": day, "source": self.name}
            for field, column in keep.items():
                row[field] = _num(item.get(column)) if column else None
            rows.append(row)
        return rows

    def etf_share_snapshot(self) -> list[dict]:
        """全市场 ETF 份额快照（东财），**按它自己的数据日期落库**。

        为什么单开一个方法：深交所那个接口只给"最新份额"、而且**无视日期参数**
        （实测 09-17 和 09-18 两次请求返回一模一样），所以深市 ETF 的份额变化
        没法补历史，只能"每天存一次快照"往前攒。而快照必须按它自己的数据日期存——
        拿"今天"去盖，就会造出一串假的"较前一日 0.00%"（踩过）。

        一次请求覆盖 1600+ 只 ETF（沪+深），成本和一个代码的查询一样。
        """
        from .base import exchange_of

        ak = self._ak()
        try:
            frame = ak.fund_etf_spot_em()
        except Exception as exc:
            raise DataSourceError(f"东财 ETF 份额快照不可用：{exc}") from exc
        rows: list[dict] = []
        for record in frame.to_dict("records"):
            symbol = _symbol(record.get("代码"))
            shares = _num(record.get("最新份额"))
            day = record.get("数据日期")
            day = str(day)[:10] if day is not None else None
            if not symbol or not shares or not day:
                continue
            market = exchange_of(symbol)
            rows.append({
                "code": f"{market}{symbol}",
                "trade_date": day,
                "shares": shares,
                "nav": None,
                "close": _num(record.get("最新价")),
                "premium_rate": _num(record.get("基金折价率")),
                "assets": None,
                "is_estimated": 1,      # 东财快照，不是交易所按日披露的那份
                "source": self.name,
            })
        if not rows:
            raise DataSourceError("东财 ETF 份额快照是空的")
        return rows

    def etf_shares(self, codes, trade_date: str) -> list[dict]:
        """ETF 份额与净值。

        份额是 T+1 披露的，所以按"从 trade_date 往前找最近一期"处理：
          沪市：上交所 ETF 产品列表（带统计日期，最准）
          深市：akshare 的 fund_etf_scale_szse 当前直接抛错，退到新浪"最近总份额"
        用非当日口径填的数一律打 is_estimated=1，不假装是当天的数。
        """
        ak = self._ak()
        wanted = {_symbol(split_code(code)[1]): code for code in codes}
        sh_symbols = {s for s, c in wanted.items() if c.upper().startswith("SH")}
        info: dict[str, dict] = {}
        errors: list[str] = []

        def slot(symbol: str) -> dict:
            return info.setdefault(wanted[symbol], {})

        # 沪市：交易所口径，从当天往前找最近一期披露
        today = trade_date.replace("-", "")
        for date_text in _recent_dates(trade_date, 12):
            if not sh_symbols:
                break
            try:
                frame = ak.fund_etf_scale_sse(date=date_text)
            except Exception as exc:
                if not _is_no_data(exc):
                    errors.append(f"上交所份额({date_text})：{exc}")
                continue
            if len(frame) == 0:
                continue
            for row in frame.to_dict("records"):
                symbol = _symbol(row.get("基金代码"))
                if symbol in sh_symbols:
                    data = slot(symbol)
                    data["shares"] = _num(row.get("基金份额"))
                    if date_text != today:
                        data["is_estimated"] = 1
            break

        # 还没拿到份额的（深市，或沪市当日尚未披露）→ 新浪"最近总份额"
        missing = [code for code in wanted.values() if not info.get(code, {}).get("shares")]
        if missing:
            try:
                frame = ak.fund_scale_open_sina()
                for row in frame.to_dict("records"):
                    symbol = _symbol(row.get("基金代码"))
                    if wanted.get(symbol) in missing:
                        data = slot(symbol)
                        data["shares"] = _num(row.get("最近总份额"))
                        data.setdefault("nav", _num(row.get("单位净值")))
                        data["is_estimated"] = 1
            except Exception as exc:
                errors.append(f"新浪份额：{exc}")

        # 这里刻意不拉全市场 ETF 行情（要翻十几页）：收盘价由上层用当天的日线补，
        # 折溢价和规模也交给上层一起算，省一次网络请求。
        rows: list[dict] = []
        for code, data in info.items():
            if not (data.get("shares") or data.get("nav")):
                continue
            shares, nav, close = data.get("shares"), data.get("nav"), data.get("close")
            rows.append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "shares": shares,
                    "nav": nav,
                    "close": close,
                    "premium_rate": round((close / nav - 1) * 100, 4) if (close and nav) else None,
                    "assets": round(shares * nav, 2) if (shares and nav) else None,
                    "is_estimated": data.get("is_estimated", 0),
                    "source": self.name,
                }
            )
        if not rows:
            raise DataSourceError("akshare 未取到 ETF 份额：" + _brief(errors))
        return rows

    # ---- 融资融券 ----

    def margin(self, trade_date: str) -> list[dict]:
        """沪深两市融资融券余额汇总。交易所 T+1 披露，当天查不到就往前找。"""
        ak = self._ak()
        rows: list[dict] = []
        errors: list[str] = []
        fetched = datetime.now().strftime("%Y-%m-%d")

        # 上交所：按日查，披露单位是元
        for date_text in _recent_dates(trade_date, 10):
            try:
                frame = ak.stock_margin_sse(start_date=date_text, end_date=date_text)
            except Exception as exc:
                if not _is_no_data(exc):
                    errors.append(f"沪市({date_text})：{exc}")
                continue
            if len(frame) == 0:
                continue
            for row in frame.to_dict("records"):
                rows.append(
                    {
                        "trade_date": trade_date,
                        "market": "SH",
                        "data_date": _dash(row.get("信用交易日期")) or _dash(date_text),
                        "fetched_date": fetched,
                        "financing_balance": _num(row.get("融资余额")),
                        "securities_lending": _num(row.get("融券余量金额")),
                        "total": _num(row.get("融资融券余额")),
                        "net_buy": _num(row.get("融资买入额")),
                        "source": self.name,
                    }
                )
            break

        # 深交所：按日查，披露单位是亿元（实测 20260910：融资余额 12714.83），统一换算成元
        for date_text in _recent_dates(trade_date, 10):
            try:
                frame = ak.stock_margin_szse(date=date_text)
            except Exception as exc:
                if not _is_no_data(exc):
                    errors.append(f"深市({date_text})：{exc}")
                continue
            if len(frame) == 0:
                continue
            row = frame.to_dict("records")[0]
            yi = 1e8   # 亿元 → 元
            financing = _num(row.get("融资余额"))
            lending = _num(row.get("融券余额"))
            total = _num(row.get("融资融券余额"))
            rows.append(
                {
                    "trade_date": trade_date,
                    "market": "SZ",
                    "data_date": _dash(date_text),
                    "fetched_date": fetched,
                    "financing_balance": financing * yi if financing is not None else None,
                    "securities_lending": lending * yi if lending is not None else None,
                    "total": total * yi if total is not None else None,
                    "net_buy": None,   # 深交所汇总接口不含融资买入额
                    "source": self.name,
                }
            )
            break

        if not rows:
            raise DataSourceError("akshare 未取到融资融券：" + _brief(errors))
        return rows
