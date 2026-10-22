"""主线识别：只回答"该看哪里"，不产生买入信号。

笔记里的三维交叉验证 + 三关持续性，落到本地数据上：
  ① 价格强度：行业成分股 20 日收益的中位数，排前三；
  ② 资金结构：行业成交额占全市场的 20 日趋势在抬升；
  ③ 扩散度：行业内 20 日涨幅 ≥20% 的成分股占比；
  ④ 持续性：最近 3 个周度快照里，至少 3 次排在前三。

为什么用中位数而不是均值：一只妖股能把行业均值拉得很漂亮，而"扩散"才是主线
与个股行情的分界。中位数 + 扩散度一起看，单票暴动无法伪装成板块行情。

边界（必须一起读）：
  - 行业标签是**当前快照**，回测过去是近似口径；成分股频繁变更的精细结论不适用；
  - 成交额占比重在**趋势**，不在绝对值；不同行业的市值结构不可横比；
  - 这里产出的是"观察地图"，任何信号仍要过风险层与赔率门槛。
"""

from __future__ import annotations

from . import industries

DEFAULTS = {
    "return_days": 20,
    "flow_window": 20,
    "top_n": 3,
    "min_members": 5,
    "diffusion_pct": 20.0,
    "new_high_days": 60,
    "persistence_weeks": 3,
    "min_persistence": 3,
    "min_coverage_pct": 80.0,
}


class MainlineError(RuntimeError):
    """给人看的错误：缺行业、样本不够、缺 pandas。"""


def settings(cfg: dict | None = None) -> dict:
    raw = (cfg or {}).get("mainline") or {}
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if raw.get(key) is not None:
            out[key] = float(raw[key]) if isinstance(DEFAULTS[key], float) else int(raw[key])
    return out


def _pandas():
    try:
        import pandas as pd
    except ImportError as exc:               # pragma: no cover - 取决于环境
        raise MainlineError("主线识别要用 pandas：python -m pip install pandas") from exc
    return pd


def _load(conn, days: int):
    pd = _pandas()
    frame = pd.read_sql_query(
        """SELECT b.code, b.trade_date, b.close, b.high, b.amount, i.industry
             FROM bars_daily b JOIN instruments i ON i.code=b.code
            WHERE COALESCE(b.quality_flag,'ok')!='blocked'
              AND (i.type='stock' OR i.type IS NULL)
              AND i.industry IS NOT NULL AND i.industry <> ''
            ORDER BY b.code, b.trade_date""",
        conn,
    )
    if frame.empty:
        raise MainlineError("没有带行业标签的个股日线：先跑 18-全市场同步 与 industries --sync")
    dates = sorted(frame["trade_date"].unique())
    if len(dates) > days:
        frame = frame[frame["trade_date"].isin(dates[-days:])]
    for column in ("code", "industry"):
        frame[column] = frame[column].astype("category")
    return frame


def _metrics(frame, cfg: dict | None = None):
    pd = _pandas()
    conf = settings(cfg)
    frame = frame.sort_values(["code", "trade_date"]).copy()
    grouped = frame.groupby("code", observed=True)
    frame["ret"] = grouped["close"].pct_change(int(conf["return_days"])) * 100
    prior_high = grouped["high"].transform(
        lambda series: series.shift(1).rolling(int(conf["new_high_days"]), min_periods=30).max())
    frame["new_high"] = frame["close"] >= prior_high
    daily = (
        frame.groupby(["trade_date", "industry"], observed=True)["amount"]
        .sum().reset_index()
    )
    daily["total"] = daily.groupby("trade_date")["amount"].transform("sum")
    daily["share"] = daily["amount"] / daily["total"].replace(0, pd.NA)
    shares = daily.pivot(index="trade_date", columns="industry", values="share")
    return frame, shares


def _snapshot(frame, shares, trade_date: str, cfg: dict | None = None) -> list[dict]:
    conf = settings(cfg)
    pd = _pandas()
    today = frame[frame["trade_date"] == trade_date]
    if today.empty:
        return []
    agg = today.groupby("industry", observed=True).agg(
        members=("code", "size"),
        ret20=("ret", "median"),
        advancers=("ret", lambda s: int((s > 0).sum())),
        diffusion=("ret", lambda s: float((s >= float(conf["diffusion_pct"])).mean() * 100)),
        new_high_share=("new_high", lambda s: float(s.mean() * 100)),
    )
    agg = agg[agg["members"] >= int(conf["min_members"])]
    if agg.empty:
        return []
    agg = agg.sort_values("ret20", ascending=False)
    agg["rank"] = range(1, len(agg) + 1)

    # 成交占比的 20 日均线 vs 前 20 日均线。窗口不够就给 None，不当 0。
    window = int(conf["flow_window"])
    out = []
    for industry, row in agg.iterrows():
        series = shares[industry].dropna() if industry in shares.columns else pd.Series(dtype=float)
        current = series.loc[:trade_date]
        last = current.iloc[-window:]
        prev = current.iloc[-(window * 2):-window]
        share_ma = float(last.mean() * 100) if len(last) >= window else None
        share_prev = float(prev.mean() * 100) if len(prev) >= window else None
        trend = (share_ma - share_prev) if (share_ma is not None and share_prev is not None) else None
        out.append({
            "industry": str(industry),
            "members": int(row["members"]),
            "ret20": round(float(row["ret20"]), 2),
            "advancers": int(row["advancers"]),
            "diffusion": round(float(row["diffusion"]), 1),
            "new_high_share": round(float(row["new_high_share"]), 1),
            "amount_share": round(share_ma, 2) if share_ma is not None else None,
            "share_trend_pp": round(trend, 3) if trend is not None else None,
            "rank": int(row["rank"]),
        })
    return out


def build(conn, cfg: dict | None = None, trade_date: str | None = None,
          top: int | None = None) -> dict:
    conf = settings(cfg)
    cov = industries.coverage(conn)
    if cov["coverage"] < float(conf["min_coverage_pct"]):
        raise MainlineError(
            f"行业覆盖只有 {cov['coverage']:.1f}%（低于 {conf['min_coverage_pct']:.0f}%），"
            "不做板块结论。先跑 python run.py industries --sync"
        )
    days = (max(int(conf["return_days"]), int(conf["new_high_days"]))
            + int(conf["flow_window"]) * 2 + int(conf["persistence_weeks"]) * 5 + 5)
    frame, shares = _metrics(_load(conn, days), cfg)
    dates = sorted(frame["trade_date"].unique())
    trade_date = trade_date or dates[-1]
    if trade_date not in set(dates):
        raise MainlineError(f"{trade_date} 没有带行业标签的日线")

    weeks: list[str] = []
    for step in range(int(conf["persistence_weeks"])):
        day = dates[max(0, len(dates) - 1 - step * 5)]
        if day not in weeks:
            weeks.append(day)
    snapshots = {day: _snapshot(frame, shares, day, cfg) for day in weeks}
    current = snapshots.get(trade_date) or _snapshot(frame, shares, trade_date, cfg)
    if not current:
        raise MainlineError("当天没有达到最小成分股数的行业")

    top_n = int(top or conf["top_n"])
    counts: dict[str, int] = {}
    for day in weeks:
        for item in snapshots.get(day) or []:
            if item["rank"] <= top_n:
                counts[item["industry"]] = counts.get(item["industry"], 0) + 1
    min_weeks = min(int(conf["min_persistence"]), len(weeks))
    for item in current:
        item["top_weeks"] = counts.get(item["industry"], 0)
        price_ok = item["rank"] <= top_n
        flow_ok = item["share_trend_pp"] is not None and item["share_trend_pp"] > 0
        diffusion_ok = item["diffusion"] >= float(conf["diffusion_pct"])
        persist_ok = item["top_weeks"] >= min_weeks
        item["gates"] = {"price": price_ok, "flow": flow_ok,
                         "diffusion": diffusion_ok, "persistence": persist_ok}
        if price_ok and flow_ok and diffusion_ok and persist_ok:
            item["verdict"] = "主线候选"
        elif price_ok:
            item["verdict"] = "事件驱动"
        else:
            item["verdict"] = "观察"

    return {
        "ok": True,
        "trade_date": trade_date,
        "industries": current,
        "mainlines": [item for item in current if item["verdict"] == "主线候选"],
        "events": [item for item in current if item["verdict"] == "事件驱动"],
        "coverage": cov,
        "weeks": weeks,
        "settings": conf,
    }


def report(result: dict) -> str:
    if not result.get("ok"):
        return result.get("message", "主线识别失败")
    conf = result["settings"]
    lines = ["===== 主线识别 =====",
             f"{result['trade_date']}｜行业口径覆盖 {result['coverage']['coverage']:.1f}%"
             f"（{result['coverage']['industries']} 个行业）"]
    lines.append("")
    lines.append(f"20 日涨幅前三：")
    for item in result["industries"][:3]:
        trend = (f"占比 {item['amount_share']:.2f}%，20 日 "
                 f"{item['share_trend_pp']:+.3f}pp" if item["share_trend_pp"] is not None
                 else "占比趋势样本不足")
        lines.append(f"  {item['rank']}. {item['industry']}：{item['ret20']:+.2f}%，"
                     f"{trend}，扩散 {item['diffusion']:.0f}%，"
                     f"新高占比 {item['new_high_share']:.0f}%，在榜 {item['top_weeks']} 周")
    for item in result["mainlines"]:
        lines.append("")
        lines.append(f"✓ 主线候选：{item['industry']}——价格、成交占比、扩散、持续性四关全过。")
    if result["events"]:
        lines.append("")
        lines.append("事件驱动（有价格强度，但资金/扩散/持续性未全过）：")
        for item in result["events"]:
            failed = [name for name, ok in item["gates"].items() if not ok]
            lines.append(f"  · {item['industry']}（未过：{'、'.join(failed)}）")
    if not result["mainlines"]:
        lines.append("")
        lines.append("今天没有四关全过的主线——这很常见。没有主线时，观察比动手更便宜。")
    lines.append("")
    lines.append(f"口径：20 日收益中位数；成交占比 {conf['flow_window']} 日均线 vs 前 "
                 f"{conf['flow_window']} 日；扩散 = 涨幅 ≥{conf['diffusion_pct']:.0f}% 的成分股占比；"
                 f"持续性 = 最近 {len(result['weeks'])} 个周度快照进前三。")
    lines.append("这只是一张'该看哪里'的地图，不是买入信号。")
    return "\n".join(lines)
