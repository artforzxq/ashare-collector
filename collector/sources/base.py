"""数据源接口与统一字段约定。

约定优于配置：所有适配器都必须把数据整理成这里定义的规范字段，
上层（校验、特征、决策）只认规范字段，不认各家返回的原始列名。
"""

from __future__ import annotations

from typing import Any, Iterable

DAILY_FIELDS = (
    "code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "pct_chg",
    "adj_factor",
    "close_adj",
)

# 各家返回的列名 → 规范字段
COLUMN_ALIASES: dict[str, str] = {
    "日期": "trade_date",
    "date": "trade_date",
    "时间": "trade_date",
    "开盘": "open",
    "open": "open",
    "收盘": "close",
    "close": "close",
    "最高": "high",
    "high": "high",
    "最低": "low",
    "low": "low",
    "成交量": "volume",
    "volume": "volume",
    "成交额": "amount",
    "amount": "amount",
    "涨跌幅": "pct_chg",
    "pctchg": "pct_chg",
    "pct_chg": "pct_chg",
    "换手率": "turnover_rate",
    "turn": "turnover_rate",
    "turnover_rate": "turnover_rate",
    "昨收": "pre_close",
    "preclose": "pre_close",
    "pre_close": "pre_close",
    "代码": "code",
    "code": "code",
    "名称": "name",
    "name": "name",
    "最新价": "close",
    "份额": "shares",
    "shares": "shares",
    "净值": "nav",
    "nav": "nav",
    "融资余额": "financing_balance",
    "融券余额": "securities_lending",
}


class DataSourceError(RuntimeError):
    """数据源不可用或返回异常。"""


def exchange_of(symbol: str) -> str:
    """6 位代码 → 交易所前缀（SH / SZ / BJ）。

    各家接口给的代码多数不带交易所，落库前统一补上，观察池里一律用 SH510300 这种写法。
    """
    text = str(symbol or "").strip().upper()
    if text[:2] in ("SH", "SZ", "BJ"):
        return text[:2]
    if "." in text:
        # sh.600000：前缀说了算，不能只看号段（sh.000300 是指数，sz.000300 不存在但号段会判成深市）
        head = text.split(".")[0]
        if head in ("SH", "SZ", "BJ"):
            return head
    text = text.split(".")[0].zfill(6)
    head = text[:2]
    if head in ("60", "68", "90") or text.startswith(("5", "11", "13")):
        return "SH"
    if head in ("00", "30", "20") or text.startswith(("12", "15", "16", "18")):
        return "SZ"
    if head in ("43", "83", "87", "88", "92"):
        return "BJ"
    return "SH"


def normalize_symbol(value: str) -> str:
    """把各家接口的写法统一成 6 位数字代码。

    sh.600000 / sh600000 / SH600000 / 600000 / 600000.SH 都会变成 600000。
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "." in text:
        # sh.600000 取后半段；600000.sh 取前半段
        head, _, tail = text.partition(".")
        text = tail if head in ("sh", "sz", "bj") else head
    if text[:2] in ("sh", "sz", "bj"):
        text = text[2:]
    return text.zfill(6)


def _exchange_hint(value: str) -> str:
    """值本身带交易所就听它的（sh.000001 是指数，000001 是平安银行，不能混）。"""
    text = str(value or "").strip().upper()
    head = text[:2]
    if head in ("SH", "SZ", "BJ"):
        return head
    if "." in text:
        head = text.split(".")[0]
        if head in ("SH", "SZ", "BJ"):
            return head
    return ""


# 个股 / ETF（含 LOF、场内货币基金）/ 指数的代码段。债券、B 股、逆回购不在里面——
# 全市场代码表要的是"能看能买的品种"，不是"这个市场里所有能报价的东西"。
_STOCK_HEADS_SH = ("600", "601", "603", "605", "688", "689")
_STOCK_HEADS_SZ = ("000", "001", "002", "003", "300", "301", "302")
_STOCK_HEADS_BJ = ("43", "83", "87", "88", "92")
_FUND_HEADS_SZ = ("15", "16", "18")


def classify_symbol(value: str) -> str:
    """6 位代码 → stock 个股 / etf 场内基金 / index 指数 / other 其它。

    指数和个股在代码上会撞车（sh.000001 是上证指数，sz.000001 是平安银行），
    所以带不带交易所前缀必须认真对待：带了就听它的，没带才按号段猜。
    """
    symbol = normalize_symbol(value)
    if len(symbol) != 6 or not symbol.isdigit():
        return "other"
    exchange = _exchange_hint(value) or exchange_of(symbol)

    if exchange == "SH":
        if symbol.startswith("000"):
            return "index"                      # 上证指数系列
        if symbol.startswith("5"):
            return "etf"                        # 51x/56x/58x 等 ETF 与场内基金
        return "stock" if symbol.startswith(_STOCK_HEADS_SH) else "other"
    if exchange == "SZ":
        if symbol.startswith("399"):
            return "index"
        if symbol.startswith(_FUND_HEADS_SZ):
            return "etf"                        # 159 ETF / 16x LOF / 18x 封闭式
        return "stock" if symbol.startswith(_STOCK_HEADS_SZ) else "other"
    if exchange == "BJ":
        if symbol.startswith("899"):
            return "index"                      # 北证 50 这类
        return "stock" if symbol.startswith(_STOCK_HEADS_BJ) else "other"
    return "other"


class BaseSource:
    name = "base"
    capabilities: set[str] = set()

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}

    # ---- 能力探测 ----
    def is_available(self) -> tuple[bool, str]:
        """返回 (是否可用, 说明)。默认认为可用。"""
        return True, "ok"

    def _missing(self, capability: str):
        raise DataSourceError(f"数据源 {self.name} 不支持 {capability}")

    # ---- 接口 ----
    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        self._missing("daily_bars")

    def market_snapshot(self, trade_date: str | None = None, codes=None) -> list[dict]:
        """全市场快照。需要枚举全市场的源（东财）可以忽略 codes；
        只提供批量报价的源（腾讯）必须拿到 codes——它自己列不出代码表。"""
        self._missing("market_snapshot")

    def intraday_snapshot(self, codes: Iterable[str]) -> list[dict]:
        self._missing("intraday_snapshot")

    def intraday_bars(self, code: str, period: int = 1) -> list[dict]:
        """当日分时线：一分钟一个点（period=1）。不支持的源在这里报错。"""
        self._missing("intraday_bars")

    def etf_shares(self, codes: Iterable[str], trade_date: str) -> list[dict]:
        self._missing("etf_shares")

    def margin(self, trade_date: str) -> list[dict]:
        self._missing("margin")

    def trade_calendar(self, start: str, end: str) -> list[dict]:
        self._missing("trade_calendar")


def canonicalize_frame(df) -> list[dict]:
    """把 pandas DataFrame 的列名映射为规范字段，并补齐 pre_close / pct_chg。"""
    if df is None or len(df) == 0:
        return []

    renamed = {}
    for column in df.columns:
        key = str(column).strip().lower()
        target = COLUMN_ALIASES.get(str(column).strip()) or COLUMN_ALIASES.get(key)
        if target:
            renamed[column] = target
    frame = df.rename(columns=renamed)

    rows: list[dict] = []
    for record in frame.to_dict("records"):
        row = {}
        for key, value in record.items():
            if key not in DAILY_FIELDS and key not in ("shares", "nav", "name", "financing_balance", "securities_lending"):
                continue
            if value is None or (isinstance(value, float) and value != value):
                row[key] = None
            else:
                row[key] = value
        if "trade_date" in row:
            row["trade_date"] = str(row["trade_date"])[:10]
        rows.append(row)

    rows.sort(key=lambda r: r.get("trade_date") or "")
    for order, row in enumerate(rows):
        if not row.get("pre_close"):
            row["pre_close"] = rows[order - 1].get("close") if order else None
        pre_close, close = row.get("pre_close"), row.get("close")
        if row.get("pct_chg") is None and pre_close and close:
            row["pct_chg"] = round((close / pre_close - 1) * 100, 4)
        row.setdefault("adj_factor", 1.0)
        row["close_adj"] = row.get("close")
    return rows
