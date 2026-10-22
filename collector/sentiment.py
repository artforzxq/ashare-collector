"""情绪阶段：冰点 / 启动 / 发酵 / 高潮 / 退潮。

定位（照笔记的原话）：**它是油门，不是方向盘**。它只回答"现在该更保守还是
更积极"，不回答"下一只龙头是谁"，也不产生任何选股信号。

所以这里只做一个**影子标签**：写进 market_breadth、在页面和命令里显示，
但不进 risk、不进 alerts、不改仓位。原因和项目所有新因子一样——
阶段阈值是经验值，必须先被 replay 证明"高阶段日之后 20 日超额更差"，
才配拿到权限。直接让它驱动仓位，等于用一个未验证的合成值替代已验证的市场层。

指标全部来自本地已有的 market_breadth（`python run.py breadth` 会补齐）：
  涨停/跌停家数、炸板家数、最高连板、上涨占比、成交额。
数据不足 5 个交易日就返回"未定"——宁可不说，也别拿两天数据猜周期。
"""

from __future__ import annotations

import statistics

from . import db

STAGES = ("冰点", "启动", "发酵", "高潮", "退潮")


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def classify(history: list[dict]) -> dict:
    """给一天定阶段。history 是按日期升序、截至当天的 market_breadth 行。"""
    if not history:
        return {"sentiment_stage": "未定", "sentiment_score": None,
                "sentiment_note": "还没有市场广度数据"}
    today = history[-1]
    if len(history) < 5:
        return {"sentiment_stage": "未定", "sentiment_score": None,
                "sentiment_note": f"历史只有 {len(history)} 个交易日，至少 5 天才能判阶段"}
    if today.get("max_boards") is None:
        # 缺连板数据时绝不能按 0 板算：那会把"没算"误判成"冰点/退潮"。
        # 先跑 python run.py breadth --backfill 把最高连板和炸板补齐，再回来看阶段。
        return {"sentiment_stage": "未定", "sentiment_score": None,
                "sentiment_note": "缺最高连板数据（先跑 python run.py breadth --backfill）"}

    def number(row, key, default=0.0):
        value = row.get(key)
        return float(value) if value is not None else float(default)

    window = history[-21:]
    prev = window[:-1]
    boards = number(today, "max_boards")
    prev_boards = [number(row, "max_boards") for row in prev]
    up = number(today, "limit_up_count")
    down = number(today, "limit_down_count")
    broken = number(today, "broken_limit_count")
    up_ratio = number(today, "up_ratio", 0.5)
    next_avg = today.get("limit_up_next_avg")
    first_next = today.get("first_board_next_avg")
    denom = up + broken
    broken_rate = broken / denom if denom > 0 else 0.0
    recent_up = [number(row, "limit_up_count") for row in prev[-5:]]
    recent_boards = prev_boards[-5:]
    max20 = max(prev_boards) if prev_boards else 0.0
    up_p80 = _percentile([number(row, "limit_up_count") for row in prev], 0.8)
    amount = number(today, "total_amount")
    amounts = [number(row, "total_amount") for row in prev if row.get("total_amount")]
    amount_ratio = amount / statistics.median(amounts) if amount and amounts else None

    # 顺序很重要：先判风险最高的一端（退潮/高潮），再判冰点，最后才是启动/发酵。
    # 退潮的特征是"龙头断板 + 涨停减少"，比单看涨停总数可靠——广度大时持续度最差。
    if (prev_boards and boards < max(prev_boards) - 1
            and recent_up and up < statistics.mean(recent_up) * 0.8) or \
            (broken_rate >= 0.5 and prev_boards and boards < max(prev_boards)):
        stage = "退潮"
    elif up >= max(1.0, up_p80) and prev_boards and boards >= max20 - 1 and up_ratio >= 0.55:
        stage = "高潮"
    elif boards <= 2 and up_ratio < 0.45:
        stage = "冰点"
    elif boards >= 2 and (not recent_boards or boards > max(recent_boards)) and up_ratio >= 0.45:
        stage = "启动"
    elif boards >= 3 and up_ratio >= 0.5:
        stage = "发酵"
    else:
        stage = "未定"

    score = min(1.0, max(0.0, 0.4 * min(boards / 6.0, 1.0)
                         + 0.3 * min(max(up_ratio, 0.0), 1.0)
                         + 0.3 * (1.0 - min(broken_rate, 1.0))))
    note = (f"最高 {boards:g} 连板、涨停 {up:g}、跌停 {down:g}、"
            f"炸板率 {broken_rate:.0%}、上涨占比 {up_ratio:.2f}")
    if next_avg is not None:
        note += f"、连板次日 {float(next_avg):+.2f}%（{today.get('limit_up_next_count') or 0} 只）"
    if first_next is not None:
        note += f"、首板次日 {float(first_next):+.2f}%（{today.get('first_board_next_count') or 0} 只）"
    if amount_ratio is not None:
        note += f"、成交额为近 20 日中位数的 {amount_ratio:.2f} 倍"
    return {
        "sentiment_stage": stage,
        "sentiment_score": round(score, 3),
        "sentiment_note": note,
    }


def update(conn, cfg: dict | None = None, trade_date: str | None = None) -> dict | None:
    """算并写回某一天的情绪标签。只写市场广度表，不碰任何决策表。"""
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS day FROM market_breadth")
        trade_date = row["day"] if row else None
    if not trade_date:
        return None
    history = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, max_boards, broken_limit_count, limit_up_count,
                      limit_down_count, up_ratio, total_amount, limit_up_next_avg,
                      limit_up_next_count, first_board_next_avg, first_board_next_count
                 FROM market_breadth WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 21""",
            (trade_date,),
        )
    ][::-1]
    result = classify(history)
    conn.execute(
        """UPDATE market_breadth SET sentiment_stage=?, sentiment_score=?, sentiment_note=?
            WHERE trade_date=?""",
        (result["sentiment_stage"], result["sentiment_score"], result["sentiment_note"], trade_date),
    )
    conn.commit()
    return {"trade_date": trade_date, **result}


def backfill(conn, cfg: dict | None = None, dates: list[str] | None = None,
             verbose: bool = False) -> int:
    """给一批日期补情绪标签（按各自当时能看到的历史算）。"""
    if dates is None:
        dates = [row["trade_date"] for row in db.query(
            conn, "SELECT trade_date FROM market_breadth ORDER BY trade_date")]
    written = 0
    for day in dates:
        if update(conn, cfg, day):
            written += 1
    if verbose and written:
        print(f"  情绪阶段：写入 {written} 个交易日（影子标签，不驱动仓位）")
    return written


def latest(conn, cfg: dict | None = None) -> dict | None:
    row = db.query_one(
        conn,
        """SELECT trade_date, sentiment_stage, sentiment_score, sentiment_note,
                  max_boards, broken_limit_count, limit_up_count, limit_down_count,
                  up_ratio, total_amount, limit_up_next_avg, limit_up_next_count,
                  first_board_next_avg, first_board_next_count
             FROM market_breadth ORDER BY trade_date DESC LIMIT 1""",
    )
    return dict(row) if row else None


def latest_available_premium(conn, trade_date: str | None = None) -> dict | None:
    """最近一个"次日溢价可计算"的交易日。

    连板次日溢价需要下一交易日的行情：最新交易日当天一定算不出来。
    所以周卡显示最近可算的那一天，并明确标日期，不把"没有"当成"0"。
    """
    sql = """SELECT trade_date, limit_up_next_avg, limit_up_next_count,
                    first_board_next_avg, first_board_next_count
               FROM market_breadth
              WHERE limit_up_next_avg IS NOT NULL"""
    params: tuple = ()
    if trade_date:
        sql += " AND trade_date<=?"
        params = (trade_date,)
    sql += " ORDER BY trade_date DESC LIMIT 1"
    row = db.query_one(conn, sql, params)
    return dict(row) if row else None
