"""影子标签的样本外检验：情绪阶段 / 主线四关到底有没有用。

为什么要单独做：`sentiment_stage` 和 `主线候选` 都是我们新造的合成值。
没经过回放就让它影响仓位，等于用"看起来合理"替代证据。这个模块只回答：
  1. 不同情绪阶段之后 20 日市场收益是否真的不同？
  2. 通过 0/1/2/3/4 关的行业，之后 20 日超额是否单调改善？

口径：
  - 前瞻收益 = 次日收盘建仓、持有 N 个交易日（与项目其它复盘一致）；
  - 全市场对照 = 同期个股收益中位数（不是 0）；
  - 情绪按每 5 个交易日抽样，降低 20 日窗口重叠造成的伪显著；
  - 主线按每 5 个交易日抽样，先按交易日聚合再算 t；
  - 行业标签是**当前快照**，历史回看是近似口径，结论只能当方向证据。
"""

from __future__ import annotations

import statistics
from pathlib import Path

from . import db, mainline, sentiment, stats

DEFAULT_HORIZON = 20
DEFAULT_STEP = 5


def _pandas():
    try:
        import pandas as pd
    except ImportError as exc:               # pragma: no cover
        raise RuntimeError("证据检验要用 pandas：python -m pip install pandas") from exc
    return pd


def _market_forward(conn, horizon: int, step: int):
    """全市场等权日收益 → 累计指数 → 前瞻收益。"""
    pd = _pandas()
    frame = pd.read_sql_query(
        """SELECT b.trade_date, AVG(b.pct_chg) AS ret
             FROM bars_daily b LEFT JOIN instruments i ON i.code=b.code
            WHERE b.pct_chg IS NOT NULL
              AND (i.type IS NULL OR i.type='stock')
            GROUP BY b.trade_date ORDER BY b.trade_date""",
        conn,
    )
    if frame.empty:
        return frame
    frame["cum"] = (1 + frame["ret"] / 100).cumprod()
    frame["fwd"] = (frame["cum"].shift(-1 - horizon) / frame["cum"].shift(-1) - 1) * 100
    frame["sample"] = False
    frame.loc[frame.index[::step], "sample"] = True
    return frame


def sentiment_evidence(conn, cfg: dict | None = None, horizon: int = DEFAULT_HORIZON,
                       step: int = DEFAULT_STEP) -> dict:
    market = _market_forward(conn, horizon, step)
    if market.empty:
        return {"ok": False, "message": "还没有个股日线，算不了前瞻收益"}
    labels = {
        row["trade_date"]: row["sentiment_stage"]
        for row in db.query(
            conn, "SELECT trade_date, sentiment_stage FROM market_breadth WHERE sentiment_stage IS NOT NULL")
    }
    market["stage"] = market["trade_date"].map(labels)
    sampled = market[market["sample"] & market["fwd"].notna() & market["stage"].isin(sentiment.STAGES)]
    if sampled.empty:
        return {"ok": False, "message": "还没有足够的历史情绪标签与前瞻收益"}
    all_days = {row.trade_date: [float(row.fwd)] for row in sampled.itertuples()}
    baseline = stats.daily_mean_stats(all_days)
    rows = []
    for stage in sentiment.STAGES:
        part = sampled[sampled["stage"] == stage]
        by_day = {row.trade_date: [float(row.fwd)] for row in part.itertuples()}
        result = stats.daily_mean_stats(by_day)
        excess = (result["mean"] - baseline["mean"]) if (result["mean"] is not None and baseline["mean"] is not None) else None
        p = stats.two_sided_p(result["t"])
        rows.append({
            "stage": stage,
            "days": result["days"],
            "mean": round(result["mean"], 2) if result["mean"] is not None else None,
            "excess": round(excess, 2) if excess is not None else None,
            "win_rate": round((part["fwd"] > 0).mean() * 100, 1) if len(part) else None,
            "t": result["t"],
            "p": p,
            "p_adj": stats.sidak_adjust(p, len(sentiment.STAGES)),
        })
    return {
        "ok": True,
        "horizon": horizon,
        "step": step,
        "baseline": round(baseline["mean"], 2) if baseline["mean"] is not None else None,
        "sample_days": len(sampled),
        "rows": rows,
    }


def _mainline_frame(conn, cfg: dict | None, horizon: int):
    pd = _pandas()
    conf = mainline.settings(cfg)
    days = (max(int(conf["return_days"]), int(conf["new_high_days"]))
            + int(conf["flow_window"]) * 2 + int(conf["persistence_weeks"]) * 5
            + horizon + 20)
    frame, shares = mainline._metrics(mainline._load(conn, days), cfg)
    frame = frame.sort_values(["code", "trade_date"]).copy()
    grouped = frame.groupby("code", observed=True)
    entry = grouped["close"].shift(-1)
    frame["fwd"] = (grouped["close"].shift(-1 - horizon) / entry - 1) * 100
    return frame, shares, conf


def _industry_daily(frame):
    pd = _pandas()
    return (
        frame.groupby(["trade_date", "industry"], observed=True)
        .agg(members=("code", "size"),
             ret20=("ret", "median"),
             diffusion=("ret", lambda s: float((s >= 20).mean() * 100)),
             fwd=("fwd", "median"))
        .reset_index()
    )


def mainline_evidence(conn, cfg: dict | None = None, horizon: int = DEFAULT_HORIZON,
                      step: int = DEFAULT_STEP) -> dict:
    pd = _pandas()
    frame, shares, conf = _mainline_frame(conn, cfg, horizon)
    daily = _industry_daily(frame)
    if daily.empty:
        return {"ok": False, "message": "没有带行业标签的个股日线"}
    market = frame.groupby("trade_date")["fwd"].median().rename("market_fwd")
    daily = daily.join(market, on="trade_date")
    daily["excess"] = daily["fwd"] - daily["market_fwd"]
    dates = sorted(daily["trade_date"].unique())
    snapshot_dates = dates[::step]
    top_sets: list[set[str]] = []
    observations = []
    for day in snapshot_dates:
        part = daily[daily["trade_date"] == day]
        part = part[part["members"] >= int(conf["min_members"])]
        if part.empty:
            continue
        part = part.sort_values("ret20", ascending=False).reset_index(drop=True)
        part["rank"] = part.index + 1
        current_top = set(part[part["rank"] <= int(conf["top_n"])]["industry"])
        top_sets.append(current_top)
        recent = top_sets[-int(conf["persistence_weeks"]):]
        for row in part.itertuples():
            series = shares[row.industry].dropna().loc[:day] if row.industry in shares.columns else pd.Series(dtype=float)
            window = int(conf["flow_window"])
            last = series.iloc[-window:]
            prev = series.iloc[-(window * 2):-window]
            trend = ((last.mean() - prev.mean()) * 100) if (len(last) >= window and len(prev) >= window) else None
            top_count = sum(row.industry in top for top in recent)
            gates = [
                row.rank <= int(conf["top_n"]),
                trend is not None and trend > 0,
                row.diffusion >= float(conf["diffusion_pct"]),
                top_count >= min(int(conf["min_persistence"]), len(recent)),
            ]
            observations.append({
                "trade_date": day, "industry": row.industry,
                "gates": sum(bool(value) for value in gates),
                "excess": float(row.excess) if pd.notna(row.excess) else None,
            })
    observations = [item for item in observations if item["excess"] is not None]
    if not observations:
        return {"ok": False, "message": "没有足够的前瞻收益样本"}

    rows = []
    for gate_count in range(5):
        part = [item for item in observations if item["gates"] == gate_count]
        by_day: dict[str, list[float]] = {}
        for item in part:
            by_day.setdefault(item["trade_date"], []).append(item["excess"])
        result = stats.daily_mean_stats(by_day)
        rows.append({
            "gates": gate_count,
            "days": result["days"],
            "samples": len(part),
            "excess": round(result["mean"], 2) if result["mean"] is not None else None,
            "win_rate": round(sum(1 for item in part if item["excess"] > 0) / len(part) * 100, 1) if part else None,
            "t": result["t"],
            "p": stats.two_sided_p(result["t"]),
        })

    # 同日配对差：四关 vs 零关。比"两个分组的均值各算一次"更干净。
    by_day_all = {}
    for item in observations:
        by_day_all.setdefault(item["trade_date"], {"0": [], "4": []})
        if item["gates"] in (0, 4):
            by_day_all[item["trade_date"]][str(item["gates"])].append(item["excess"])
    paired = {
        day: [statistics.mean(values["4"]) - statistics.mean(values["0"])]
        for day, values in by_day_all.items() if values["0"] and values["4"]
    }
    diff = stats.daily_mean_stats(paired)
    return {
        "ok": True,
        "horizon": horizon,
        "step": step,
        "rows": rows,
        "paired": {
            "days": diff["days"],
            "diff": round(diff["mean"], 2) if diff["mean"] is not None else None,
            "t": diff["t"],
            "p": stats.two_sided_p(diff["t"]),
        },
        "caveat": "行业分类是当前快照，历史回看为近似口径；结论只作方向证据。",
    }


def report(sent: dict, line: dict) -> str:
    lines = ["===== 影子标签证据检验 ====="]
    if not sent.get("ok"):
        lines.append(f"情绪：{sent.get('message')}")
    else:
        lines.append("")
        lines.append(f"情绪阶段（持有 {sent['horizon']} 日；每 {sent['step']} 个交易日抽样；"
                     f"全样本均值 {sent['baseline']:+.2f}%）")
        lines.append(f"{'阶段':<6}{'样本日':>7}{'均值':>9}{'超额':>9}{'胜率':>8}{'t':>7}{'校正p':>8}")
        for row in sent["rows"]:
            if not row["days"]:
                lines.append(f"{row['stage']:<6}{0:>7}{'—':>9}{'—':>9}{'—':>8}{'—':>7}{'—':>8}")
                continue
            lines.append(f"{row['stage']:<6}{row['days']:>7}{row['mean']:>+8.2f}%"
                         f"{row['excess']:>+8.2f}%{row['win_rate']:>7.0f}%"
                         f"{row['t']:>7.2f}{row['p_adj']:>8.3f}")
        lines.append("  看**校正 p < 0.05 且超额方向符合预期**：发酵/启动为正、退潮/高潮为负。"
                     "不显著就继续当影子标签。")
        thin = [row["stage"] for row in sent["rows"] if 0 < row["days"] < 10]
        if thin:
            lines.append(f"  样本警告：{'、'.join(thin)} 不足 10 个采样日，只能当方向看。")
    if not line.get("ok"):
        lines.append("")
        lines.append(f"主线：{line.get('message')}")
    else:
        lines.append("")
        lines.append(f"主线四关（持有 {line['horizon']} 日；每 {line['step']} 个交易日抽样）")
        lines.append(f"{'通过关数':<9}{'交易日':>8}{'样本':>7}{'行业超额':>10}{'胜率':>8}{'t':>7}{'p':>8}")
        for row in line["rows"]:
            excess = f"{row['excess']:+.2f}%" if row["excess"] is not None else "—"
            win = f"{row['win_rate']:.0f}%" if row["win_rate"] is not None else "—"
            t = f"{row['t']:.2f}" if row["t"] is not None else "—"
            p = f"{row['p']:.3f}" if row["p"] is not None else "—"
            lines.append(f"{row['gates']:<9}{row['days']:>8}{row['samples']:>7}{excess:>10}{win:>8}{t:>7}{p:>8}")
        paired = line["paired"]
        if paired["days"]:
            lines.append(f"  同日配对：四关 − 零关 = {paired['diff']:+.2f}%"
                         f"（{paired['days']} 个交易日，t={paired['t']}，p={paired['p']:.3f}）")
            if paired["days"] < 10:
                lines.append("  ⚠ 配对样本不足 10 个交易日，即使 p<0.05 也不能据此转正；"
                             "继续影子运行，等样本长出来。")
        gate4 = next((row for row in line["rows"] if row["gates"] == 4), None)
        if gate4 and gate4["samples"] < 30:
            lines.append(f"  ⚠ 四关组只有 {gate4['samples']} 个行业-日样本，"
                         "不足以证明它是稳定规律。")
        lines.append("  只有四关组稳定跑赢零关组，才说明这套合成值得继续加权限。")
        lines.append(f"  {line['caveat']}")
    lines.append("")
    lines.append("注意：这些是方向证据，不是新的买卖信号；样本外检验也不能消除行业口径近似。")
    return "\n".join(lines)


def save_report(text: str, root: str | Path = ".") -> Path:
    target = Path(root) / "回测" / f"证据-{db.now_iso()[:10]}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
