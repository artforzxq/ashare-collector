"""市场层（Beta）：把"今天整体是什么行情"变成一个能计算的数。

为什么单独一层：个股层回答"买什么"（Alpha），市场层回答"该用多大仓位、该不该出手"（Beta）。
这两件事的证据强度完全不同——我们的历史重放已经说明，靠形态挑个股几乎没有超额；
而"市场整体强弱"是另一类信息，它不该混进个股趋势分里（那会变成用大盘给个股打分），
只该在风险层出现（仓位）。这也是外面那套"Alpha/Beta 分离"说法里唯一站得住的部分。

三个输入，各代表一件事，缺一个都不下结论：
  · **宽基指数状态**（同一套状态机算出来的 up/range/down）——趋势在不在；
  · **市场广度**（涨跌家数比，本地 K 线自己算的）——上涨是不是有宽度；
  · **成交额**（相对自己近 60 个交易日的中位数）——有没有量。
三层合成 0–1：0.5 是中性，越高越"进攻"，越低越"防守"。

纪律：**只记录，不进决策**（和 ADX、换手率、摆动结构一样先当影子因子）。
要进风险层，得先跑一遍历史回放，看"市场层高的日子，我们的信号是不是真的更好"——
通不过就永远停在记录里。
"""

from __future__ import annotations

import statistics

from . import db, features
from .registry import FactorRegistry

# 默认盯的宽基：沪市综合 + 沪深300 + 创业板 + 科创50。
# 覆盖大盘 / 大盘成长 / 小盘成长 / 硬科技四张脸，比单看上证更能代表"今天是什么行情"。
DEFAULT_INDICES = ("SH000001", "SH000300", "SZ399006", "SH000688")
DEFAULT_WEIGHTS = {"index": 0.4, "breadth": 0.4, "amount": 0.2}
NEUTRAL = 0.5


def settings(cfg: dict | None = None) -> dict:
    section = ((cfg or {}).get("market") or {})
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(section.get("weights") or {})
    return {
        "indices": list(section.get("indices") or DEFAULT_INDICES),
        "weights": weights,
        "amount_window": int(section.get("amount_window", 60)),
        "amount_low": float(section.get("amount_low", 0.7)),
        "amount_high": float(section.get("amount_high", 1.3)),
    }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _index_scores(conn, cfg: dict, indices: list[str], trade_date: str) -> dict[str, float]:
    """每只宽基在每个交易日的状态分：up=1 / range=0.5 / down=0。

    用**同一套状态机**（features.compute_feature_series）算，不另立一个口径——
    否则"指数状态"和"个股状态"会用两把尺子，市场层与个股层就没法对话了。
    """
    by_date: dict[str, list[float]] = {}
    for code in indices:
        rows = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT * FROM bars_daily WHERE code=? AND trade_date<=?
                   AND COALESCE(quality_flag,'ok')!='blocked' ORDER BY trade_date""",
                (code, trade_date),
            )
        ]
        if len(rows) < 80:
            continue
        series = features.compute_feature_series(
            rows, cfg, FactorRegistry(cfg.get("factors", []), "regime"), extra={})
        for row in series:
            state = row.get("state")
            if state not in ("up", "range", "down"):
                continue
            by_date.setdefault(row["trade_date"], []).append(
                {"up": 1.0, "range": 0.5, "down": 0.0}[state])
    return {day: statistics.mean(values) for day, values in by_date.items() if values}


def series(conn, cfg: dict, trade_date: str | None = None) -> dict[str, float]:
    """每个交易日的市场层分数（0–1）。缺数据的日期直接不出现，不拿 0.5 冒充。"""
    conf = settings(cfg)
    trade_date = trade_date or db.query_one(
        conn, "SELECT MAX(trade_date) AS d FROM bars_daily")["d"]
    if not trade_date:
        return {}

    index_score = _index_scores(conn, cfg, conf["indices"], trade_date)
    breadth_rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, up_ratio, total_amount FROM market_breadth
               WHERE trade_date<=? ORDER BY trade_date""",
            (trade_date,),
        )
    ]
    amounts = [float(row["total_amount"]) for row in breadth_rows if row.get("total_amount")]

    weights = conf["weights"]
    window = conf["amount_window"]
    out: dict[str, float] = {}
    for position, row in enumerate(breadth_rows):
        day = row["trade_date"]
        parts: list[tuple[float, float]] = []        # (权重, 分数)
        if day in index_score:
            parts.append((weights["index"], index_score[day]))
        if row.get("up_ratio") is not None:
            parts.append((weights["breadth"], _clamp(float(row["up_ratio"]))))
        if row.get("total_amount"):
            history = amounts[max(0, position - window):position]     # 不含当日
            if len(history) >= 10:
                median = statistics.median(history)
                if median > 0:
                    ratio = float(row["total_amount"]) / median
                    span = conf["amount_high"] - conf["amount_low"]
                    parts.append((weights["amount"],
                                  _clamp((ratio - conf["amount_low"]) / span)))
        if len(parts) < 2:
            continue                     # 只有一块输入时不下结论
        total_weight = sum(weight for weight, _ in parts)
        out[day] = round(sum(weight * value for weight, value in parts) / total_weight, 4)
    return out


def latest(conn, cfg: dict, trade_date: str | None = None) -> dict:
    """给页面/简报用：最新一天的市场层，连同三块输入各自的读数。"""
    conf = settings(cfg)
    values = series(conn, cfg, trade_date)
    if not values:
        return {"ok": False, "message": "还没有市场层数据（要广度 + 宽基指数状态至少各一天）"}
    day = max(values)
    return {
        "ok": True,
        "trade_date": day,
        "score": values[day],
        "stance": stance(values[day]),
        "weights": conf["weights"],
        "indices": conf["indices"],
        "history": sorted(values.items())[-20:],
    }


def stance(score: float | None) -> str:
    """0–1 的市场层翻译成人话。分档只是**读法**，不是交易指令。"""
    if score is None:
        return "无"
    if score >= 0.6:
        return "进取"
    if score <= 0.4:
        return "防守"
    return "中性"


# 资产选择：市场层 → 该偏向哪一类工具。只用来给筛选池**排序和提示**，
# 不改任何筛选口径——口径一改，"筛出来的票后来怎么样"就没法跨时间比了。
DEFAULT_MIX = {
    "defense": {"broad_etf": 0.6, "sector_etf": 0.1, "stock": 0.3},
    "neutral": {"broad_etf": 0.4, "sector_etf": 0.3, "stock": 0.3},
    "offense": {"broad_etf": 0.2, "sector_etf": 0.3, "stock": 0.5},
}
KIND_LABEL = {"broad_etf": "宽基 ETF", "sector_etf": "行业/主题 ETF",
              "stock": "个股", "index": "指数", "other": "其它"}


def settings_mix(cfg: dict | None = None) -> dict:
    section = ((cfg or {}).get("market") or {})
    mix = {key: dict(value) for key, value in DEFAULT_MIX.items()}
    for key, value in (section.get("asset_mix") or {}).items():
        if key in mix and isinstance(value, dict):
            mix[key].update(value)
    return mix


def kind_of(cfg: dict | None, code: str, kind: str | None = None) -> str:
    """这只标的属于哪一类工具：宽基 ETF / 行业主题 ETF / 个股 / 指数。

    宽基名单来自配置（`market.broad_etfs`）——靠名字猜"是不是宽基"不可靠
    （"中证 A500"、"科创 50"、"XX 龙头"都长得像），宁可显式列出来。
    """
    section = ((cfg or {}).get("market") or {})
    broad = {str(item).upper() for item in (section.get("broad_etfs") or [])}
    text = str(code or "").upper()
    if kind == "index":
        return "index"
    if kind == "etf":
        return "broad_etf" if text in broad else "sector_etf"
    if kind == "stock":
        return "stock"
    return "broad_etf" if text in broad else "other"


def asset_mix(cfg: dict | None, score: float | None) -> dict:
    """市场层 → 资产选择的建议配比（只用于排序与提示）。

    市场层还没数据时**按中性处理**，但把 available 标成 False——
    排序总得有个默认，但不能让人以为"系统判断现在是中性"。
    """
    weights = settings_mix(cfg)
    key = {"防守": "defense", "中性": "neutral", "进取": "offense"}.get(stance(score), "neutral")
    chosen = dict(weights[key])
    preferred = max(chosen, key=chosen.get) if chosen else "stock"
    return {
        "available": score is not None,
        "score": score,
        "stance": stance(score) if score is not None else "中性（无数据）",
        "key": key,
        "weights": chosen,
        "preferred": preferred,
        "preferred_label": KIND_LABEL.get(preferred, preferred),
    }


def explain(score: float | None) -> str:
    """一行说明：分数 + 档位。给简报和页面用。"""
    if score is None:
        return "市场层：还没有数据"
    return f"市场层 {score:.2f}（{stance(score)}）"
