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
  - **上市头几天的规则不一样**，这也是最容易把新股数据误判成脏数据的地方：
      · 创业板、科创板：上市后**前 5 个交易日不设涨跌幅**，第 6 日起 20%；
      · 北交所：**上市首日不设涨跌幅**，次日起 30%；
      · 主板：首日相对**发行价**涨 44% / 跌 36%，次日起 10%（ST 5%）。
    传 trading_days（上市第几个交易日，首日=1）就能按阶段判；不传就按已过观察期处理。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

LIMIT_MAIN = 10.0
LIMIT_STAR = 20.0     # 科创板
LIMIT_GEM = 20.0      # 创业板
LIMIT_BJ = 30.0       # 北交所
LIMIT_ST = 5.0        # 主板 ST
LIMIT_MAIN_FIRST = 44.0    # 主板新股首日（涨幅，相对发行价）
LIMIT_MAIN_FIRST_DOWN = 36.0   # 主板新股首日跌幅上限
NO_LIMIT_DAYS_STAR_GEM = 5     # 创业板、科创板：前 5 个交易日不设涨跌幅


def is_registration_board(code: str) -> bool:
    """注册制板块（创业板 / 科创板）：新股前 5 个交易日不设涨跌幅。"""
    text = (code or "").strip().upper()
    digits = text[2:] if text[:2] in ("SH", "SZ", "BJ") else text
    return digits.startswith("688") or digits.startswith("30")


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


def limit_pct(code: str, name: str | None = None, trading_days: int | None = None,
              up: bool = True) -> float | None:
    """当日涨跌幅上限（百分比）。**返回 None 表示当天不设涨跌幅限制**。

    trading_days = 上市第几个交易日（首日 1）；None 表示未知，按"已经过了观察期"处理。
    """
    text = (code or "").strip().upper()
    digits = text[2:] if text[:2] in ("SH", "SZ", "BJ") else text
    days = int(trading_days) if trading_days else None

    if exchange(code) == "BJ":
        if days == 1:
            return None                     # 北交所新股首日不设限
        return LIMIT_BJ
    if is_registration_board(code):         # 科创板 / 创业板
        if days is not None and days <= NO_LIMIT_DAYS_STAR_GEM:
            return None                     # 前 5 个交易日不设限
        return LIMIT_STAR
    if days == 1 and not is_st(name):
        return LIMIT_MAIN_FIRST if up else LIMIT_MAIN_FIRST_DOWN
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


def at_limit(bar: dict, code: str = "", name: str | None = None, up: bool = True,
             trading_days: int | None = None) -> bool:
    """收盘价是否压在涨/跌停板上。

    优先用前收盘价算出的涨跌停价（精确）；库里的前收盘价缺失时退回用涨跌幅判断，
    这时容差放宽到 0.35 个百分点，因为低价股的涨跌停幅度会有零点几个百分点的浮动。

    不设涨跌幅的那些天（新股前几日）一律返回 False：那天根本没有"板"可封。
    """
    close = bar.get("close")
    if close is None:
        return False
    if str(bar.get("quality_flag") or "ok") == "blocked":
        return False          # 数据体检都不认这根，就别拿它当封板（否则脏数据会被数成连板）
    pct = limit_pct(code or str(bar.get("code") or ""), name, trading_days, up=up)
    if pct is None:
        return False
    pre_close = bar.get("pre_close")
    if pre_close:
        target = limit_price(float(pre_close), pct, up=up)
        return float(close) + 1e-6 >= target if up else float(close) - 1e-6 <= target
    change = _pct_change(bar)
    if change is None:
        return False
    return change >= pct - 0.35 if up else change <= -(pct - 0.35)


def at_limit_up(bar: dict, code: str = "", name: str | None = None,
                trading_days: int | None = None) -> bool:
    return at_limit(bar, code, name, up=True, trading_days=trading_days)


def at_limit_down(bar: dict, code: str = "", name: str | None = None,
                  trading_days: int | None = None) -> bool:
    return at_limit(bar, code, name, up=False, trading_days=trading_days)


def at_limit_price_hit(bar: dict, code: str = "", name: str | None = None, up: bool = True,
                       trading_days: int | None = None) -> bool:
    """盘中是否**摸到过**涨（跌）停价：用最高价（最低价）比，不看收盘。

    和 at_limit_up 的区别：那个问"收盘封没封住"，这个问"今天碰没碰到"。
    两个一起用就能认出"炸板"——碰到了、但没封住。
    """
    price = bar.get("high") if up else bar.get("low")
    pre_close = bar.get("pre_close")
    if price is None or not pre_close:
        return False                      # 没有最高价/前收就判不了，宁可为 False
    if str(bar.get("quality_flag") or "ok") == "blocked":
        return False
    pct = limit_pct(code or str(bar.get("code") or ""), name, trading_days, up=up)
    if pct is None:
        return False                      # 不设限的日子，谈不上"摸到涨停价"
    target = limit_price(float(pre_close), pct, up=up)
    return float(price) + 1e-6 >= target if up else float(price) - 1e-6 <= target


def max_move_pct(code: str, name: str | None, trading_days: int | None, cfg: dict | None,
                 up: bool = True) -> float:
    """这一根 K 线的涨跌幅"上限"：超过它就该怀疑数据，而不是怀疑行情。

    以前是一刀切 11%，于是创业板/科创板正常的 20%、北交所的 30%、
    新股上市头几天的不设限，全都被当成跳变阻断。现在按法定幅度算，
    再加一点容差（四舍五入到分造成的零点几个百分点）。

    不设涨跌幅的那些天没有"上限"可言，但仍要拦明显离谱的数据——
    用 validation.jump_pct_limit_unlimited（默认 300%）。
    """
    section = ((cfg or {}).get("validation") or {})
    tol = float(section.get("jump_tol_pct", 1.0))
    limit = limit_pct(code, name, trading_days, up=up)
    if limit is None:
        return float(section.get("jump_pct_limit_unlimited", 300.0))
    return limit + tol
