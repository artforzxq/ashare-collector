"""往返成本口径：全项目只在这里算一次。

为什么单独成模块：回测、复盘、分档研究、风险层各算一套，就会出现
"回测扣了 0.302%、提醒复盘还是毛收益、风险层干脆没扣"这种自相矛盾的局面。
同一笔交易，不管从哪个页面看，成本都必须一样。

默认口径（config.yaml → backtest.cost）：
  佣金 万 2.5 × 2（买卖各一次）
  印花税 万 5（仅卖出）
  过户费 0.1 基点 × 2
  滑点 20 基点（一条往返）
合计 30.2 基点 = 0.302%，和笔记里的"实战口径 0.3% 起算"对齐。

显性费用（0.102%）是交易所和券商真会收的；滑点不是账单上的一行，
但它是真实发生的成本——尤其是小盘、题材、快市。只算显性费用，
等于系统性地高估每一笔交易的期望值。

小额单笔还有一层隐性惩罚：券商佣金普遍有单笔最低 5 元。
传了 `notional`（单笔成交金额）才会把这条算进去——归一化的收益率回测
没有"多少钱"这个概念，硬套一个金额反而是假精确。风险层和账本能传金额。
"""

from __future__ import annotations

DEFAULTS = {
    "commission_bps": 2.5,   # 单边佣金，万分之 2.5
    "stamp_bps": 5.0,        # 印花税，卖出单边
    "transfer_bps": 0.1,     # 过户费，双边
    "slippage_bps": 20.0,    # 滑点，一条往返（常规 0.1%–0.3%，取中值偏低）
    "min_commission_cny": 5.0,
}


def settings(cfg: dict | None = None) -> dict:
    """合并配置与默认值。缺字段就用默认，不要求用户把五项都写全。"""
    raw = ((cfg or {}).get("backtest") or {}).get("cost") or {}
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if raw.get(key) is not None:
            out[key] = float(raw[key])
    return out


def round_trip_pct(cfg: dict | None = None, notional: float | None = None) -> float:
    """一次买入 + 一次卖出的总成本，返回百分数（0.302 表示 0.302%）。

    notional 给了，就按"单笔最低佣金"重算佣金那一项：
    8000 元单笔、5 元最低佣金 → 单边 0.0625%，往返 0.125%，
    是名义万 2.5 的 2.5 倍。
    """
    conf = settings(cfg)
    commission_bps = conf["commission_bps"]
    if notional and notional > 0:
        half = max(float(notional) * commission_bps / 1e4, conf["min_commission_cny"])
        commission_bps = half / float(notional) * 1e4
    total_bps = (commission_bps * 2 + conf["stamp_bps"]
                 + conf["transfer_bps"] * 2 + conf["slippage_bps"])
    return round(total_bps / 100, 6)


def cost_in_r(cfg: dict | None = None, stop_pct: float | None = None,
              notional: float | None = None) -> float | None:
    """成本折算成 R：往返成本 ÷ 止损宽度。

    止损 8%、成本 0.302% → 0.0378R。这不是小数：一笔 1R 的交易，
    光摩擦就吃掉了 3.8% 的风险预算。
    """
    if not stop_pct or float(stop_pct) <= 0:
        return None
    return round(round_trip_pct(cfg, notional) / float(stop_pct), 6)


def describe(cfg: dict | None = None, notional: float | None = None) -> str:
    """一行说明成本由哪些部分组成，给报告和页面用。"""
    conf = settings(cfg)
    total = round_trip_pct(cfg, notional)
    parts = [
        f"佣金 {conf['commission_bps'] * 2 / 100:.3f}%",
        f"印花 {conf['stamp_bps'] / 100:.3f}%",
        f"过户 {conf['transfer_bps'] * 2 / 100:.3f}%",
        f"滑点 {conf['slippage_bps'] / 100:.3f}%",
    ]
    tail = f"；按单笔 {notional:,.0f} 元含 {conf['min_commission_cny']:.0f} 元佣金下限" if notional else ""
    return f"往返 {total:.3f}%（" + " + ".join(parts) + f"）{tail}"
