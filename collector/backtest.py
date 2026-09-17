"""参数回测：把状态机的阈值放到历史数据上重跑，看哪组参数真的有效。

三个刻意的口径选择，都是为了不自欺：
  1. 用生产代码里同一个状态机函数（`features._apply_state_machine`），回测就是实盘的那套逻辑；
  2. 信号日收盘确认、**次日收盘建仓**，不占用未来数据；
  3. 除了看绝对收益，还算**基准**——同一批标的里"随便哪天买入"的平均收益。
     信号跑不赢基准，就说明它没有信息量，涨只是因为这批标的那段时间本来就在涨。

样本量说明：观察池 10 只、约一年日线，统计功效很弱。报告里会把这句话写进去，
不要拿几个月的数据当定论。
"""

from __future__ import annotations

import statistics
from datetime import datetime
from pathlib import Path

from . import db, features as features_mod, review
from .config import watchlist_codes
from .registry import FactorRegistry

HORIZONS = (5, 20)
DEFAULT_GRID = {
    "enter_up": (60, 65, 70, 75),
    "confirm_days": (1, 2, 3),
    "min_state_days": (1, 3, 5),
}
# 机会分闸门这一维刻意不做：机会分是"状态 + 是否贴关键带 + 是否放量突破"的确定性函数，
# 上升状态下它必然 ≥0.6，所以拿 0.6 去过滤等于没过滤（实测两行结果完全一样）。
DEFAULT_GATES = (0.0,)
MIN_BARS = 80


def load_bars(conn, cfg) -> dict[str, list[dict]]:
    """观察池里每只标的的日线（跳过被阻断的）。"""
    out: dict[str, list[dict]] = {}
    for item in watchlist_codes(cfg):
        bars = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT * FROM bars_daily WHERE code=? AND COALESCE(quality_flag,'ok')!='blocked'
                   ORDER BY trade_date""",
                (item["code"],),
            )
        ]
        if len(bars) >= MIN_BARS:
            out[item["code"]] = bars
    return out


def base_features(bars: list[dict], cfg: dict) -> list[dict]:
    """按当前配置算一遍特征。状态参数换成别的组合时不用重算这一层。"""
    registry = FactorRegistry(cfg.get("factors", []), "backtest")
    return features_mod.compute_feature_series(bars, cfg, registry, {})


def apply_params(rows: list[dict], params: dict) -> list[dict]:
    """只换状态机参数重跑一遍（用的是生产代码里同一个函数）。"""
    slim = [
        {"trade_date": row["trade_date"], "trend_score": row["trend_score"], "state": row["state"]}
        for row in rows
    ]
    features_mod._apply_state_machine(slim, params)
    return slim


def make_params(enter_up: float, confirm_days: int, min_state_days: int) -> dict:
    """对称地推出另外三个阈值：退出比进入低 15 分，做空侧镜像。"""
    return {
        "enter_up": enter_up,
        "exit_up": enter_up - 15,
        "enter_down": 100 - enter_up,
        "exit_down": 100 - enter_up + 15,
        "confirm_days": confirm_days,
        "min_state_days": min_state_days,
        "neutral_band": 0.10,
    }


def forward_arrays(bars: dict[str, list[dict]]) -> dict[str, dict[int, list[float | None]]]:
    """每只标的、每个交易日、每个持有期的前瞻收益（只算一次，网格里复用）。"""
    out: dict[str, dict[int, list[float | None]]] = {}
    for code, rows in bars.items():
        dates = [row["trade_date"] for row in rows]
        out[code] = {h: [review.forward_return(rows, dates, day, h) for day in dates] for h in HORIZONS}
    return out


def _clean(values) -> list[float]:
    return [v for v in values if v is not None]


def evaluate(
    bars: dict[str, list[dict]],
    base: dict[str, list[dict]],
    fwd: dict[str, dict[int, list[float | None]]],
    params: dict,
    min_opportunity: float = 0.0,
) -> dict:
    """跑一组参数：统计信号次数、胜率、平均收益、超额和持有期回撤。"""
    signal_returns: dict[int, list[float]] = {h: [] for h in HORIZONS}
    drawdowns: list[float] = []
    signal_count = 0

    for code, rows in bars.items():
        states = apply_params(base[code], params)
        for index, state_row in enumerate(states):
            if not (state_row.get("state_switched") and state_row["state"] == "up"):
                continue
            if min_opportunity:
                merged = {**base[code][index], "state": state_row["state"]}
                if features_mod._opportunity_score(merged) < min_opportunity:
                    continue
            signal_count += 1
            for horizon in HORIZONS:
                value = fwd[code][horizon][index]
                if value is not None:
                    signal_returns[horizon].append(value)
            entry = index + 1
            exit_pos = entry + 19
            if entry < len(rows) and exit_pos < len(rows):
                entry_close = rows[entry].get("close")
                if entry_close:
                    lowest = min(bar["low"] for bar in rows[entry:exit_pos + 1])
                    drawdowns.append((lowest / entry_close - 1) * 100)

    baseline = {h: _clean([v for code in fwd for v in fwd[code][h]]) for h in HORIZONS}
    result = {"params": params, "min_opportunity": min_opportunity}
    result["signals"] = signal_count
    for horizon in HORIZONS:
        values = signal_returns[horizon]
        base_values = baseline[horizon]
        avg = statistics.mean(values) if values else None
        base_avg = statistics.mean(base_values) if base_values else None
        result[f"n{horizon}"] = len(values)
        result[f"win{horizon}"] = (sum(1 for v in values if v > 0) / len(values) * 100) if values else None
        result[f"avg{horizon}"] = avg
        result[f"median{horizon}"] = statistics.median(values) if values else None
        result[f"base{horizon}"] = base_avg
        result[f"excess{horizon}"] = (avg - base_avg) if (avg is not None and base_avg is not None) else None
    result["mdd20"] = statistics.mean(drawdowns) if drawdowns else None
    return result


def run_grid(conn, cfg, grid: dict | None = None, gates=DEFAULT_GATES, verbose: bool = True) -> dict:
    """扫参数网格，按 20 日超额收益排序。"""
    grid = grid or DEFAULT_GRID
    bars = load_bars(conn, cfg)
    if not bars:
        return {"ok": False, "message": "没有足够的日线数据，先跑一次 3-每日任务"}
    if verbose:
        print(f"  参与回测：{len(bars)} 只标的，"
              f"{sum(len(v) for v in bars.values())} 根日线")
    base = {code: base_features(rows, cfg) for code, rows in bars.items()}
    fwd = forward_arrays(bars)

    results: list[dict] = []
    for enter_up in grid["enter_up"]:
        for confirm in grid["confirm_days"]:
            for min_days in grid["min_state_days"]:
                params = make_params(enter_up, confirm, min_days)
                for gate in gates:
                    metrics = evaluate(bars, base, fwd, params, gate)
                    metrics["label"] = f"enter_up={enter_up} 确认{confirm}日 最短{min_days}日" + (
                        f" 机会分≥{gate}" if gate else "")
                    results.append(metrics)
    results.sort(key=lambda item: (item["excess20"] if item["excess20"] is not None else -999), reverse=True)

    current = current_params(cfg)
    return {"ok": True, "results": results, "codes": list(bars),
            "bars": sum(len(v) for v in bars.values()), "current": current}


def current_params(cfg: dict) -> dict:
    state = cfg.get("state", {})
    return {
        "enter_up": state.get("enter_up", 70),
        "exit_up": state.get("exit_up", 55),
        "enter_down": state.get("enter_down", 30),
        "exit_down": state.get("exit_down", 45),
        "confirm_days": state.get("confirm_days", 2),
        "min_state_days": state.get("min_state_days", 3),
        "neutral_band": state.get("neutral_band", 0.10),
    }


def _cell(value, digits: int = 2, suffix: str = "", signed: bool = False) -> str:
    if value is None:
        return "—"
    if signed and suffix == "%":
        return f"{value:+.{digits}f}{suffix}"
    return f"{value:.{digits}f}{suffix}"


def render_report(result: dict, top: int = 15) -> str:
    """把网格结果排成人能读的表，并把当前参数标出来。"""
    lines = ["===== 参数回测 =====", ""]
    lines.append(f"标的 {len(result['codes'])} 只，日线 {result['bars']} 根")
    lines.append("口径：信号日收盘确认 → 次日收盘建仓 → 持有 5/20 个交易日")
    lines.append("基准：同一批标的里所有交易日的平均收益（信号跑不赢基准 = 没有信息量）")
    lines.append("")
    header = f"{'参数组合':<34}{'信号':>5}{'5日胜率':>8}{'5日超额':>9}{'20日胜率':>9}{'20日超额':>9}{'回撤':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    current = result["current"]
    current_label = (f"enter_up={current['enter_up']} 确认{current['confirm_days']}日 "
                     f"最短{current['min_state_days']}日")
    shown = 0
    seen: set = set()
    merged = 0
    for row in result["results"]:
        marker = " ←当前" if row["label"].startswith(current_label) else ""
        if not marker:
            if shown >= top:
                continue
            # 参数不同但在这批数据上表现完全一样的，合并掉，免得表格全是重复行
            signature = (row["signals"], row["win5"], row["excess5"], row["win20"], row["excess20"], row["mdd20"])
            if signature in seen:
                merged += 1
                continue
            seen.add(signature)
        shown += 1
        lines.append(
            f"{row['label']:<34}{row['signals']:>5}"
            f"{_cell(row['win5'], 0, '%'):>8}{_cell(row['excess5'], 2, '%', True):>9}"
            f"{_cell(row['win20'], 0, '%'):>9}{_cell(row['excess20'], 2, '%', True):>9}"
            f"{_cell(row['mdd20'], 1, '%', True):>8}{marker}"
        )
    if merged:
        lines.append(f"（另有 {merged} 组参数在这批数据上表现完全相同，已合并）")
    lines.append("")
    base5 = result["results"][0]["base5"]
    base20 = result["results"][0]["base20"]
    lines.append(f"基准（随便哪天买）：5 日 {_cell(base5, 2, '%', True)}，"
                 f"20 日 {_cell(base20, 2, '%', True)}")
    lines.append("")
    lines.append("怎么读：")
    lines.append("  1. 先看 20 日超额，它是正数才有意义；胜率高但超额为负，说明只是行情好。")
    lines.append("  2. 再看信号数：几十次以下的结果没有统计意义，别据此改参数。")
    lines.append("  3. 回撤列是持有 20 天期间的平均最大回撤，代表要忍多大的浮动。")
    lines.append("  4. 样本只有一年、十来只标的，结论是方向性的，不是定论。")
    return "\n".join(lines)


def write_report(text: str, root: str | Path) -> Path:
    target = Path(root) / "回测" / f"回测报告-{datetime.now().strftime('%Y%m%d')}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
