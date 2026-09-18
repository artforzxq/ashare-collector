"""涨跌停与可成交性判断。

回测里最容易自欺的一处：信号次日一字涨停封死，现实中你根本买不进去，
但按"次日收盘价建仓"回测却照样给你成交——每次都吃到最强势的那一段。
这里只回答一个问题：某一根 K 线，收盘价是不是压在涨跌停板上。

口径（按交易所规则）：
  - 涨跌停价 = 前收盘价 ×(1 ± 幅度)，**四舍五入到分**（A 股就是这么定的，
    所以 2 元钱的股票涨停幅度实际是 10% 上下浮动 0.25%，不能拿 9.9% 一刀切）；
  - 幅度按代码前缀判：科创板 688xxx、创业板 30xxxx → 20%，北交所 → 30%，其余 10%；
  - ST 从名称里认（库里 instruments.is_st 目前全是 0，但名称带 ST / *ST）。
    主板 ST 是 5%；创业板、科创板的 ST 仍然是 20%。

已知识别不了的：新股上市首日不设涨跌幅限制，而库里没有上市日期，
所以这类票会被当普通票处理。它们通常是小票，多半已经落进小票过滤里。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

LIMIT_MAIN = 10.0
LIMIT_STAR = 20.0     # 科创板
LIMIT_GEM = 20.0      # 创业板
LIMIT_BJ = 30.0       # 北交所
LIMIT_ST = 5.0        # 主板 ST


def exchange(code: str) -> str:
    """从代码前缀取交易所：SH / SZ / BJ。"""
    text = (code or "").strip().upper()
    if text.startswith("BJ") or text.startswith("8") or text.startswith("4"):
        return "BJ"
    if text.startswith("SH") or text.startswith("6"):
        return "SH"
    return "SZ"


def is_st(name: str | None) -> bool:
    """名称里带 ST 就算 ST（*ST 也算）。"""
    text = (name or "").upper().replace(" ", "")
    return "ST" in text


def limit_pct(code: str, name: str | None = None) -> float:
    """当日涨跌幅上限（百分比）。"""
    text = (code or "").strip().upper()
    digits = text[2:] if text[:2] in ("SH", "SZ", "BJ") else text

    if exchange(code) == "BJ":
        return LIMIT_BJ
    if digits.startswith("688"):            # 科创板
        return LIMIT_STAR
    if digits.startswith("30"):             # 创业板
        return LIMIT_GEM
    return LIMIT_ST if is_st(name) else LIMIT_MAIN


def _round_price(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_price(pre_close: float, pct: float, up: bool = True) -> float:
    """涨跌停价：前收盘价按幅度算完四舍五入到分。"""
    factor = 1.0 + pct / 100.0 if up else 1.0 - pct / 100.0
    return _round_price(pre_close * factor)


def _pct_change(bar: dict) -> float | None:
    """当日涨跌幅：优先用库里的 pct_chg，没有就用收盘价和前收算。"""
    if bar.get("pct_chg") is not None:
        return float(bar["pct_chg"])
    pre_close, close = bar.get("pre_close"), bar.get("close")
    if pre_close and close:
        return (close / pre_close - 1) * 100.0
    return None


def at_limit(bar: dict, code: str = "", name: str | None = None, up: bool = True) -> bool:
    """收盘价是否压在涨/跌停板上。

    优先用前收盘价算出的涨跌停价（精确）；库里的前收盘价缺失时退回用涨跌幅判断，
    这时容差放宽到 0.35 个百分点，因为低价股的涨跌停幅度会有零点几个百分点的浮动。
    """
    close = bar.get("close")
    if close is None:
        return False
    pct = limit_pct(code or str(bar.get("code") or ""), name)
    pre_close = bar.get("pre_close")
    if pre_close:
        target = limit_price(float(pre_close), pct, up=up)
        return float(close) + 1e-6 >= target if up else float(close) - 1e-6 <= target
    change = _pct_change(bar)
    if change is None:
        return False
    return change >= pct - 0.35 if up else change <= -(pct - 0.35)


def at_limit_up(bar: dict, code: str = "", name: str | None = None) -> bool:
    return at_limit(bar, code, name, up=True)


def at_limit_down(bar: dict, code: str = "", name: str | None = None) -> bool:
    return at_limit(bar, code, name, up=False)


def at_limit_price_hit(bar: dict, code: str = "", name: str | None = None, up: bool = True) -> bool:
    """盘中是否**摸到过**涨（跌）停价：用最高价（最低价）比，不看收盘。

    和 at_limit_up 的区别：那个问"收盘封没封住"，这个问"今天碰没碰到"。
    两个一起用就能认出"炸板"——碰到了、但没封住。
    """
    price = bar.get("high") if up else bar.get("low")
    pre_close = bar.get("pre_close")
    if price is None or not pre_close:
        return False                      # 没有最高价/前收就判不了，宁可为 False
    pct = limit_pct(code or str(bar.get("code") or ""), name)
    target = limit_price(float(pre_close), pct, up=up)
    return float(price) + 1e-6 >= target if up else float(price) - 1e-6 <= target
