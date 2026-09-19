"""参数回测：把状态机的阈值放到历史数据上重跑，看哪组参数真的有效。

五个刻意的口径选择，都是为了不自欺：
  1. 用生产代码里同一个状态机函数（`features._apply_state_machine`），回测就是实盘的那套逻辑；
  2. 信号日收盘确认、**次日收盘建仓**，不占用未来数据；
  3. 除了看绝对收益，还算**基准**——同一批标的里"随便哪天买入"的平均收益。
     信号跑不赢基准，就说明它没有信息量，涨只是因为这批标的那段时间本来就在涨；
  4. **可成交性**：次日一字涨停买不进、到期日跌停卖不掉，都不算你的收益（见 collector/limits.py）；
  5. **扣成本**：往返佣金 + 印花税 + 过户费，信号和基准扣同一笔，否则比的是谁的摩擦小。

样本口径（`backtest.universe`）：
  - `market`：本地有足够历史、且近 60 日均成交额过门槛的标的，随机抽样（固定种子，
    结果可复现）。这是默认值——十来只票的结论没有统计意义，几十次信号的标准误比要比较的
    差距还大；
  - `watchlist`：只看观察池，口径和早期版本一致，用于快速验证。

**参数要找高原，不要找尖峰**：孤立的高收益点多半是拟合出来的，所以报告里除了按超额排名，
还会给每个组合算"邻域表现"——它在参数空间里上下左右挪一格之后还站不站得住。
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

from . import db, features as features_mod, limits, universe
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
DEFAULT_SAMPLE = 400
DEFAULT_SEED = 20260917
DEFAULT_EXIT_DELAY = 3


def backtest_cfg(cfg: dict | None) -> dict:
    return (cfg or {}).get("backtest") or {}


def cost_pct(cfg: dict | None) -> float:
    """往返成本，换算成百分比。默认 佣金万 2.5×2 + 印花税万 5 + 过户费 0.1×2 = 0.102%。"""
    cost = backtest_cfg(cfg).get("cost") or {}
    commission = float(cost.get("commission_bps", 2.5)) * 2
    stamp = float(cost.get("stamp_bps", 5.0))
    transfer = float(cost.get("transfer_bps", 0.1)) * 2
    return (commission + stamp + transfer) / 100.0


def _names(conn, codes) -> dict[str, str]:
    if not codes:
        return {}
    out: dict[str, str] = {}
    for code in codes:
        row = db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (code,))
        if row and row["name"]:
            out[code] = row["name"]
    return out


def load_bars(conn, cfg, mode: str | None = None, limit: int | None = None,
              seed: int | None = None, verbose: bool = False) -> tuple[dict, dict, dict]:
    """取回测用的日线，返回 (bars, names, 样本口径信息)。

    默认走全市场抽样；`mode="watchlist"` 退回只看观察池的老口径。
    """
    section = backtest_cfg(cfg)
    mode = (mode or section.get("universe") or "watchlist").lower()
    if mode == "market":
        min_bars = int(section.get("min_bars", 120))
        picked = universe.select_codes(
            conn, cfg, min_bars=min_bars,
            limit=limit if limit is not None else section.get("sample", DEFAULT_SAMPLE),
            seed=seed if seed is not None else section.get("seed", DEFAULT_SEED),
            verbose=verbose,
        )
        codes = picked["codes"]
        need = min_bars
    else:
        codes = [item["code"] for item in watchlist_codes(cfg)]
        need = MIN_BARS
        picked = {"codes": codes, "total": len(codes), "dropped_liquidity": 0,
                  "threshold": universe.min_avg_amount(cfg), "sampled": False}

    bars: dict[str, list[dict]] = {}
    for code in codes:
        rows = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT * FROM bars_daily WHERE code=? AND COALESCE(quality_flag,'ok')!='blocked'
                   ORDER BY trade_date""",
                (code,),
            )
        ]
        if len(rows) >= need:
            bars[code] = rows
    picked["mode"] = mode
    picked["min_bars"] = need
    return bars, _names(conn, list(bars)), picked


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


def _horizon_return(rows: list[dict], entry: int, horizon: int, code: str, name: str | None,
                    limit_check: bool, delay_max: int) -> float | None:
    """次日收盘建仓 → 持有 horizon 个交易日的收益（%）。

    买不进（一字涨停）或卖不掉（跌停封死、顺延期限内都没打开）都返回 None——
    这类信号现实中拿不到，不该出现在统计里。
    """
    if entry >= len(rows):
        return None
    entry_bar = rows[entry]
    if limit_check and limits.at_limit_up(entry_bar, code, name):
        return None
    exit_pos = entry + horizon
    if exit_pos >= len(rows):
        return None
    if limit_check and delay_max > 0:
        steps = 0
        while steps < delay_max and exit_pos + 1 < len(rows) and limits.at_limit_down(rows[exit_pos], code, name):
            exit_pos += 1
            steps += 1
        if limits.at_limit_down(rows[exit_pos], code, name):
            return None
    entry_close = entry_bar.get("close")
    exit_close = rows[exit_pos].get("close")
    if not entry_close or not exit_close:
        return None
    return round((exit_close / entry_close - 1) * 100, 4)


def forward_arrays(bars: dict[str, list[dict]], names: dict | None = None,
                   limit_check: bool = True, delay_max: int = DEFAULT_EXIT_DELAY) -> dict:
    """每只标的、每个交易日、每个持有期的前瞻收益（只算一次，网格里复用）。"""
    names = names or {}
    out: dict[str, dict[int, list[float | None]]] = {}
    for code, rows in bars.items():
        name = names.get(code)
        out[code] = {
            horizon: [
                _horizon_return(rows, index + 1, horizon, code, name, limit_check, delay_max)
                for index in range(len(rows))
            ]
            for horizon in HORIZONS
        }
    return out


def fillable_mask(bars: dict[str, list[dict]], names: dict | None = None) -> dict[str, list[bool]]:
    """每个交易日之后能不能真的买到（次日没有涨停封死）。"""
    names = names or {}
    out: dict[str, list[bool]] = {}
    for code, rows in bars.items():
        name = names.get(code)
        flags: list[bool] = []
        for index in range(len(rows)):
            entry = index + 1
            flags.append(entry < len(rows) and not limits.at_limit_up(rows[entry], code, name))
        out[code] = flags
    return out


def _clean(values) -> list[float]:
    return [v for v in values if v is not None]


def baseline_returns(fwd: dict, cost: float = 0.0) -> dict[int, list[float]]:
    """基准：这批标的里所有交易日的平均收益（扣同一笔成本）。

    它只跟数据有关、跟参数无关，所以整个网格算一次就够了——不用每组参数重算一遍。
    """
    return {
        horizon: [value - cost for code in fwd for value in _clean(fwd[code][horizon])]
        for horizon in HORIZONS
    }


def evaluate(
    bars: dict[str, list[dict]],
    base: dict[str, list[dict]],
    fwd: dict[str, dict[int, list[float | None]]],
    params: dict,
    min_opportunity: float = 0.0,
    cost: float = 0.0,
    fillable: dict[str, list[bool]] | None = None,
    years: float | None = None,
    base_returns: dict[int, list[float]] | None = None,
) -> dict:
    """跑一组参数：统计信号次数、胜率、平均收益、净超额和持有期回撤。

    `cost` 是往返成本（%），信号和基准都扣同一笔——不扣的话，比较的是谁的摩擦小。
    """
    signal_returns: dict[int, list[float]] = {h: [] for h in HORIZONS}
    baseline = base_returns or baseline_returns(fwd, cost)
    drawdowns: list[float] = []
    signal_count = 0
    blocked = 0

    for code, rows in bars.items():
        mask = (fillable or {}).get(code)
        states = apply_params(base[code], params)
        for index, state_row in enumerate(states):
            if not (state_row.get("state_switched") and state_row["state"] == "up"):
                continue
            if min_opportunity:
                merged = {**base[code][index], "state": state_row["state"]}
                if features_mod._opportunity_score(merged) < min_opportunity:
                    continue
            if mask is not None and index < len(mask) and not mask[index]:
                blocked += 1                       # 次日涨停买不进，这个信号不算数
                continue
            signal_count += 1
            for horizon in HORIZONS:
                value = fwd[code][horizon][index]
                if value is not None:
                    signal_returns[horizon].append(value - cost)
            entry = index + 1
            exit_pos = entry + 19
            if entry < len(rows) and exit_pos < len(rows):
                entry_close = rows[entry].get("close")
                if entry_close:
                    lowest = min(bar["low"] for bar in rows[entry:exit_pos + 1])
                    drawdowns.append((lowest / entry_close - 1) * 100)

    result = {"params": params, "min_opportunity": min_opportunity}
    result["signals"] = signal_count
    result["blocked"] = blocked
    result["cost"] = cost
    result["freq"] = (signal_count / max(0.001, len(bars) * years)) if years else None
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


def _sample_years(bars: dict[str, list[dict]]) -> float | None:
    dates = [row["trade_date"] for rows in bars.values() for row in rows]
    if not dates:
        return None
    try:
        span = (datetime.fromisoformat(max(dates)) - datetime.fromisoformat(min(dates))).days
        if span > 0:
            return span / 365.25
    except ValueError:
        pass
    # 日期不是规范 ISO（合成数据、上游脏数据）时，退回按交易日估算：一年约 250 个交易日。
    # 频率只是报告里的参考量，不该因为它把整份回测打断。
    longest = max((len(rows) for rows in bars.values()), default=0)
    return longest / 250.0 if longest > 1 else None


def plateau(results: list[dict], grid: dict) -> dict[tuple, dict]:
    """参数高原：把每个组合和它"邻域"的表现放一起看。

    邻域 = 三个维度各挪 ±1 格（按 grid 里的下标），最多 26 个邻居。
    尖峰（自己很高、邻居很平甚至为负）多半是拟合；高原（自己不错、邻居大多也在 0 以上）才值得用。
    """
    names = ("enter_up", "confirm_days", "min_state_days")
    positions = {name: {value: i for i, value in enumerate(grid[name])} for name in names}
    index = {
        (row["params"]["enter_up"], row["params"]["confirm_days"], row["params"]["min_state_days"]): row
        for row in results
    }
    out: dict[tuple, dict] = {}
    for key, row in index.items():
        if any(key[i] not in positions[names[i]] for i in range(3)):
            continue                       # 结果里有 grid 之外的组合，跳过比崩掉好
        base_pos = [positions[name][value] for name, value in zip(names, key)]
        neighbor_values: list[float] = []
        for delta in [(de, dc, dm) for de in (-1, 0, 1) for dc in (-1, 0, 1) for dm in (-1, 0, 1)]:
            if delta == (0, 0, 0):
                continue
            pos = [base_pos[i] + delta[i] for i in range(3)]
            if any(pos[i] < 0 or pos[i] >= len(grid[names[i]]) for i in range(3)):
                continue
            neighbor = index.get(tuple(grid[names[i]][pos[i]] for i in range(3)))
            if neighbor is None or neighbor.get("excess20") is None:
                continue
            neighbor_values.append(neighbor["excess20"])
        own = row.get("excess20")
        if not neighbor_values or own is None:
            out[key] = {"n": len(neighbor_values), "mean": None, "worst": None,
                        "positive": None, "own": own}
            continue
        pool = neighbor_values + [own]
        out[key] = {
            "n": len(neighbor_values),
            "mean": statistics.mean(pool),
            "worst": min(neighbor_values),
            "positive": sum(1 for v in neighbor_values if v > 0) / len(neighbor_values) * 100,
            "own": own,
        }
    return out


# ---------------------------------------------------------------- 换手率研究
#
# 和状态机回测是两个问题：那个问"这套参数行不行"，这个问"放量之后到底怎么了"。
# 所以它**不经过状态机**——把全市场每个 (标的, 交易日) 按换手放量倍数分档，
# 看之后 5/20 日的收益，再减去同一批日期上"随便买一只"的等权基准。
# 只减基准这一点不能省：不减的话，放量日恰好在大盘上涨阶段就会得出"放量有用"的假结论。

# 分档：以"当日换手 ÷ 自己近 20 日均值"为准，所以天然剔除了股本规模和冷热差异
TURNOVER_BUCKETS = (
    ("缩量 <0.8", 0.0, 0.8),
    ("常态 0.8–1.2", 0.8, 1.2),
    ("温和放量 1.2–2", 1.2, 2.0),
    ("明显放量 2–3", 2.0, 3.0),
    ("暴力放量 >3", 3.0, float("inf")),
)


def _bucket_of(ratio: float) -> str | None:
    for label, low, high in TURNOVER_BUCKETS:
        if low <= ratio < high:
            return label
    return None


def turnover_study(conn, cfg, mode: str | None = None, limit: int | None = None,
                   verbose: bool = True) -> dict:
    """按换手放量倍数分档，看之后 5 / 20 日的表现（相对同日期等权基准的超额）。

    三个口径都跟回测保持一致：用生产代码算特征、次日建仓、扣同一笔成本、
    涨停买不进的样本剔除——这样这里的结论和参数回测是同一把尺子。
    """
    bars, names, sample = load_bars(conn, cfg, mode=mode, limit=limit, verbose=verbose)
    if not bars:
        return {"ok": False, "message": "没有足够的日线数据，先跑一次 3-每日任务"}
    cost = cost_pct(cfg)
    section = backtest_cfg(cfg)
    check_limits = bool(section.get("limit_check", True))
    delay_max = int(section.get("exit_delay_max", DEFAULT_EXIT_DELAY))

    fwd = forward_arrays(bars, names, limit_check=check_limits, delay_max=delay_max)
    fillable = fillable_mask(bars, names) if check_limits else None
    baseline = baseline_returns(fwd, cost)

    # bucket → horizon → 收益列表
    samples: dict[str, dict[int, list[float]]] = {
        label: {horizon: [] for horizon in HORIZONS} for label, _, _ in TURNOVER_BUCKETS
    }
    missing = 0
    for code, rows in bars.items():
        base = base_features(rows, cfg)
        for index, feature in enumerate(base):
            ratio = feature.get("turnover_ratio")
            if ratio is None:
                missing += 1
                continue
            label = _bucket_of(float(ratio))
            if label is None:
                continue
            if fillable is not None and not fillable[code][index]:
                continue          # 次日涨停买不进，这天的收益不算你的
            for horizon in HORIZONS:
                value = fwd[code][horizon][index]
                if value is not None:
                    samples[label][horizon].append(value - cost)

    rows_out: list[dict] = []
    for label, _, _ in TURNOVER_BUCKETS:
        entry = {"bucket": label}
        for horizon in HORIZONS:
            values = samples[label][horizon]
            base_values = baseline[horizon]
            entry[f"n{horizon}"] = len(values)
            entry[f"avg{horizon}"] = statistics.mean(values) if values else None
            entry[f"win{horizon}"] = (sum(1 for v in values if v > 0) / len(values) * 100) if values else None
            base_avg = statistics.mean(base_values) if base_values else None
            entry[f"base{horizon}"] = base_avg
            entry[f"excess{horizon}"] = (
                (entry[f"avg{horizon}"] - base_avg)
                if (entry[f"avg{horizon}"] is not None and base_avg is not None) else None
            )
        rows_out.append(entry)

    return {
        "ok": True,
        "buckets": rows_out,
        "covered": sum(entry["n20"] for entry in rows_out),
        "missing_turnover": missing,
        "sample": sample,
        "years": _sample_years(bars),
        "cost": cost,
        "limit_check": check_limits,
        "codes": len(bars),
    }


def render_turnover_study(result: dict, verbose: bool = True) -> str:
    """给人看的换手率研究：先看超额，再看样本量，最后看单调性。"""
    if not result.get("ok"):
        return result.get("message", "换手率研究没跑成")
    lines = ["===== 换手率与之后的收益 =====", ""]
    lines.append(f"样本：{result['codes']} 只标的，约 {_cell(result.get('years'), 1)} 年；"
                 f"往返成本 {_cell(result['cost'] * 100, 3, suffix='%')}；"
                 f"实际参与 {result['covered']} 个样本（换手率缺失跳过 {result['missing_turnover']} 个）")
    lines.append("")
    lines.append("分档按「当日换手 ÷ 自己近 20 日均值」——这样剔除了股本规模差异，")
    lines.append("小盘股不会因为天生换手高就被整档算进「暴力放量」。")
    lines.append("")
    lines.append(f"  {'分档':<16}{'样本':>8}{'5日均值':>10}{'5日超额':>10}{'20日均值':>10}{'20日超额':>10}{'20日胜率':>10}")
    for entry in result["buckets"]:
        lines.append(
            f"  {entry['bucket']:<16}{entry['n20']:>8}"
            f"{_cell(entry['avg5'], 2, suffix='%', signed=True):>10}"
            f"{_cell(entry['excess5'], 2, suffix='%', signed=True):>10}"
            f"{_cell(entry['avg20'], 2, suffix='%', signed=True):>10}"
            f"{_cell(entry['excess20'], 2, suffix='%', signed=True):>10}"
            f"{_cell(entry['win20'], 1, suffix='%'):>10}"
        )
    lines.append("")
    lines.append("怎么读：")
    lines.append("  1. 超额才是结论——均值只说「涨没涨」，超额说「比随便买一只强不强」；")
    lines.append("  2. 看**单调性**：如果放量越大之后越弱（超额一路往下），那是真的反转效应；")
    lines.append("     如果忽高忽低，多半是噪声，别据此改规则。")
    lines.append("  3. 样本少的档（几百个以下）没有统计意义，别单独下结论。")
    lines.append("  4. 这里的收益是「次日建仓、持有到期」的口径，与参数回测同一把尺子。")
    return "\n".join(lines)


def run_grid(conn, cfg, grid: dict | None = None, gates=DEFAULT_GATES, verbose: bool = True,
             mode: str | None = None, limit: int | None = None) -> dict:
    """扫参数网格：按 20 日净超额收益排序，并给出每个组合的邻域表现。"""
    grid = grid or DEFAULT_GRID
    section = backtest_cfg(cfg)
    bars, names, sample_meta = load_bars(conn, cfg, mode=mode, limit=limit, verbose=verbose)
    if not bars:
        return {"ok": False, "message": "没有足够的日线数据，先跑一次 3-每日任务"}
    if verbose:
        print(f"  参与回测：{len(bars)} 只标的，"
              f"{sum(len(v) for v in bars.values())} 根日线")
    base = {code: base_features(rows, cfg) for code, rows in bars.items()}

    delay_max = int(section.get("exit_delay_max", DEFAULT_EXIT_DELAY))
    check_limits = bool(section.get("limit_check", True))
    fwd = forward_arrays(bars, names, limit_check=check_limits, delay_max=delay_max)
    mask = fillable_mask(bars, names) if check_limits else None
    cost = cost_pct(cfg)
    years = _sample_years(bars)
    base_returns = baseline_returns(fwd, cost)

    results: list[dict] = []
    for enter_up in grid["enter_up"]:
        for confirm in grid["confirm_days"]:
            for min_days in grid["min_state_days"]:
                params = make_params(enter_up, confirm, min_days)
                for gate in gates:
                    metrics = evaluate(bars, base, fwd, params, gate,
                                       cost=cost, fillable=mask, years=years,
                                       base_returns=base_returns)
                    metrics["label"] = f"enter_up={enter_up} 确认{confirm}日 最短{min_days}日" + (
                        f" 机会分≥{gate}" if gate else "")
                    results.append(metrics)
    results.sort(key=lambda item: (item["excess20"] if item["excess20"] is not None else -999), reverse=True)

    return {
        "ok": True,
        "results": results,
        "codes": list(bars),
        "bars": sum(len(v) for v in bars.values()),
        "current": current_params(cfg),
        "sample": sample_meta,
        "cost": cost,
        "years": years,
        "limit_check": check_limits,
        "exit_delay_max": delay_max,
        "plateau": plateau(results, grid),
    }


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
    """把网格结果排成人能读的表，把当前参数标出来，并给出参数高原的读法。"""
    lines = ["===== 参数回测 =====", ""]
    sample = result.get("sample") or {}
    mode = sample.get("mode", "watchlist")
    scope = "全市场抽样" if mode == "market" else "观察池"
    lines.append(f"标的 {len(result['codes'])} 只（{scope}），日线 {result['bars']} 根")
    if mode == "market":
        dropped = sample.get("dropped_liquidity", 0)
        threshold = (sample.get("threshold") or 0) / 1e4
        note = f"标的池：本地够 {sample.get('min_bars', 0)} 根日线的共 {sample.get('total', 0) + dropped} 只"
        if dropped:
            note += f"，其中近 60 日均成交额低于 {threshold:,.0f} 万的 {dropped} 只已剔除"
        lines.append(note)
        if sample.get("sampled"):
            lines.append(f"随机抽取 {len(result['codes'])} 只（固定种子，结果可复现）")
    lines.append("口径：信号日收盘确认 → 次日收盘建仓 → 持有 5/20 个交易日；"
                 f"往返成本 {result.get('cost', 0):.3f}%")
    if result.get("limit_check"):
        lines.append("可成交性：建仓日一字涨停买不进、到期日跌停卖不掉的信号已剔除"
                     f"（跌停最多顺延 {result.get('exit_delay_max', 0)} 个交易日）")
    lines.append("基准：同一批标的里所有交易日的平均收益（同样扣成本）；跑不赢基准 = 没有信息量")
    lines.append("")
    header = (f"{'参数组合':<34}{'信号':>5}{'不可成交':>8}{'次/只/年':>9}"
              f"{'5日胜率':>8}{'20日胜率':>9}{'20日净超额':>10}{'回撤':>8}")
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
        freq = f"{row['freq']:.1f}" if row.get("freq") is not None else "—"
        lines.append(
            f"{row['label']:<34}{row['signals']:>5}{row.get('blocked', 0):>8}{freq:>9}"
            f"{_cell(row['win5'], 0, '%'):>8}"
            f"{_cell(row['win20'], 0, '%'):>9}{_cell(row['excess20'], 2, '%', True):>10}"
            f"{_cell(row['mdd20'], 1, '%', True):>8}{marker}"
        )
    if merged:
        lines.append(f"（另有 {merged} 组参数在这批数据上表现完全相同，已合并）")
    lines.append("")
    base5 = result["results"][0]["base5"]
    base20 = result["results"][0]["base20"]
    lines.append(f"基准（随便哪天买）：5 日 {_cell(base5, 2, '%', True)}，"
                 f"20 日 {_cell(base20, 2, '%', True)}")
    # 风险层的期望值口径需要胜率输入——这里给出这批数据量出来的建议值
    current_row = next((r for r in result["results"] if r["label"].startswith(current_label)), None)
    if current_row and current_row.get("win20") is not None:
        lines.append(f"当前参数的 20 日胜率 {current_row['win20']:.0f}%（{current_row['n20']} 次信号）"
                     f"—— 建议把 config.yaml 的 risk.win_rate 设成 {current_row['win20'] / 100:.2f}"
                     "（风险层用它算期望值）")

    table = result.get("plateau") or {}
    if table:
        lines.append("")
        lines.append("参数高原（自己 + 邻域）：邻域 = 三个参数各挪一格的组合")
        lines.append(f"{'参数组合':<34}{'自身净超额':>10}{'邻域均值':>9}{'邻域最差':>9}{'邻域>0占比':>11}{'邻居':>5}")
        ranked = [(key, value) for key, value in table.items() if value.get("mean") is not None]
        ranked.sort(key=lambda item: item[1]["mean"], reverse=True)
        for key, value in ranked[:top]:
            label = f"enter_up={key[0]} 确认{key[1]}日 最短{key[2]}日"
            marker = " ←当前" if label.startswith(current_label) else ""
            lines.append(
                f"{label:<34}{_cell(value['own'], 2, '%', True):>10}"
                f"{_cell(value['mean'], 2, '%', True):>9}{_cell(value['worst'], 2, '%', True):>9}"
                f"{_cell(value['positive'], 0, '%'):>11}{value['n']:>5}{marker}"
            )
        lines.append("")
        lines.append("怎么读高原：邻域均值和自身差不多、邻域最差还在 0 以上 → 这片参数都能用；")
        lines.append("            自身很高但邻域塌下去 → 尖峰，多半是拟合，别拿它去改配置。")

    lines.append("")
    lines.append("怎么读：")
    lines.append("  1. 先看 20 日净超额，它是正数才有意义；胜率高但超额为负，说明只是行情好。")
    lines.append("  2. 再看信号数：几十次以下的结果没有统计意义，别据此改参数。")
    lines.append("  3. 回撤列是持有 20 天期间的平均最大回撤，代表要忍多大的浮动。")
    lines.append("  4. 不可成交列是被剔除的信号数（涨停买不进 / 跌停卖不掉）。它占信号越高，")
    lines.append("     说明这套规则越盯着最强势的票——那部分收益现实中你拿不到。")
    if mode != "market":
        lines.append("  5. 当前是观察池口径，样本小，结论只是方向性的。")
    return "\n".join(lines)


def save_turnover(result: dict, root: str | Path) -> Path:
    """换手率研究的结构化结果落盘（页面读它，不重跑）。"""
    target = Path(root) / "回测" / f"换手率结果-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    slim = {key: value for key, value in result.items() if key != "sample"}
    target.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def latest_turnover(root: str | Path) -> dict | None:
    """最近一次换手率研究的结果；没跑过返回 None。"""
    folder = Path(root) / "回测"
    if not folder.exists():
        return None
    files = sorted(folder.glob("换手率结果-*.json"))
    if not files:
        return None
    newest = files[-1]
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data["_file"] = newest.name
    data["_saved_at"] = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return data


def write_report(text: str, root: str | Path) -> Path:
    target = Path(root) / "回测" / f"回测报告-{datetime.now().strftime('%Y%m%d')}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def save_result(result: dict, root: str | Path) -> Path:
    """把这次回测的结构化结果也存一份。

    报告是给人读的 markdown，页面要的是能排序、能高亮的表格，所以两份都留：
    markdown 用来存档和回看，json 用来给页面渲染（页面不该为看一眼结果重跑一遍回测）。

    两处要为 json 做转换：
      - `codes` 那几百个代码对页面没用，存长度就行，不然白白大几百 KB；
      - `plateau` 的键是元组（python 能当字典键，json 不能），而且页面看的是"某一行的邻域表现"，
        所以直接并进每一行里，别让前端再自己拼 key。
    """
    plateau = result.get("plateau") or {}
    rows: list[dict] = []
    for row in result.get("results") or []:
        params = row.get("params") or {}
        key = (params.get("enter_up"), params.get("confirm_days"), params.get("min_state_days"))
        item = dict(row)
        cell = plateau.get(key)
        if cell:
            item["plateau"] = cell
        rows.append(item)

    slim = {key: value for key, value in result.items() if key not in ("codes", "plateau", "results")}
    slim["results"] = rows
    slim["code_count"] = len(result.get("codes") or [])
    target = Path(root) / "回测" / f"回测结果-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def latest_result(root: str | Path) -> dict | None:
    """最近一次回测的结构化结果；没跑过就返回 None。"""
    folder = Path(root) / "回测"
    if not folder.exists():
        return None
    files = sorted(folder.glob("回测结果-*.json"))
    if not files:
        return None
    newest = files[-1]
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data["_file"] = newest.name
    data["_saved_at"] = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return data
