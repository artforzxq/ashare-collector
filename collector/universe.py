"""标的池口径：谁有资格进筛选和回测。

筛选和回测各自的历史长度要求不一样（筛选只要够算形态，回测还要留出前瞻收益的空间），
所以 `min_bars` 是各自的参数。但**成交额门槛只有一份**，写在这里——
因为"小票要不要带镣铐"这件事，最怕的就是筛选里排除了、回测里又放进来，
两边口径一旦不一致，回测就会拿筛不出来的票去证明策略有效。

为什么需要这道门槛：本地仓库是全市场同步来的，里面躺着大量一天只成交几百万的票。
它们不是"机会"，是成交不了、复权数据也经不起看的噪声。放它们进回测，
等于让回测专门去挑那些"事后涨得最猛"的幽灵票，结论会系统性偏乐观。

**指数默认不进这道池子**（`universe.include_index: false`）。两个理由：
  1. 指数不参与流动性过滤——它的 amount 是成交量或者根本取不到，拿它比没有意义，
     于是"不过滤"就等于"永远入选"；
  2. 回测的基准是"同一批标的里随便买一只"，指数混进来会让基准和信号一起变成指数的事，
     而指数既不是股票、也不是能直接下单的东西（要买得通过 ETF）。
想看指数就把它加进观察池，或者把 include_index 打开。
"""

from __future__ import annotations

import random

from . import db

# 近 60 个交易日的日均成交额下限（元）。0 表示不过滤。
DEFAULT_MIN_AVG_AMOUNT_60D = 30_000_000
AVG_DAYS = 60


def min_avg_amount(cfg: dict | None) -> float:
    """配置里的成交额门槛。缺失就取默认值，显式写 0 表示不过滤。"""
    section = (cfg or {}).get("universe") or {}
    value = section.get("min_avg_amount_60d")
    if value is None:
        return float(DEFAULT_MIN_AVG_AMOUNT_60D)
    return float(value)


def include_index(cfg: dict | None) -> bool:
    """指数要不要进标的池。默认**不进**，理由见文件头。"""
    section = (cfg or {}).get("universe") or {}
    value = section.get("include_index")
    return False if value is None else bool(value)


def looks_like_index(code: str) -> bool:
    """代码表缺失时的兜底判断：SH000xxx / SZ399xxx 是指数。"""
    text = (code or "").strip().upper()
    return text.startswith("SH000") or text.startswith("SZ399")


def _recent_adv_sql() -> str:
    return f"""
    WITH ranked AS (
      SELECT code, amount,
             ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
      FROM bars_daily
      WHERE COALESCE(quality_flag, 'ok') != 'blocked'
    )
    SELECT ranked.code                             AS code,
           COUNT(*)                                AS n,
           AVG(CASE WHEN rn <= {AVG_DAYS} THEN amount END) AS adv,
           MAX(COALESCE(instruments.type, ''))     AS kind
    FROM ranked
    LEFT JOIN instruments ON instruments.code = ranked.code
    GROUP BY ranked.code
    HAVING n >= ?
    ORDER BY ranked.code
    """


def select_codes(conn, cfg: dict | None, min_bars: int, limit: int | None = None,
                 seed: int | None = None, verbose: bool = False) -> dict:
    """按"历史够长 + 成交额够大"筛出可用标的，可再随机抽样。

    返回 {codes, total, dropped_liquidity, dropped_index, threshold, sampled}。抽样用固定种子，
    同一份数据每次抽到的票一样——回测结果要能复现，否则调参就是在追噪声。
    """
    threshold = min_avg_amount(cfg)
    keep_index = include_index(cfg)
    rows = db.query(conn, _recent_adv_sql(), (min_bars,))

    codes: list[str] = []
    dropped = 0
    dropped_index = 0
    for row in rows:
        adv = row["adv"]
        kind = (row["kind"] or "").strip().lower()
        code = row["code"]
        if kind == "index" or (not kind and looks_like_index(code)):
            if not keep_index:
                dropped_index += 1
                continue
            codes.append(code)                      # 指数不参与流动性过滤
            continue
        if threshold > 0 and (adv is None or float(adv) < threshold):
            dropped += 1
            continue
        codes.append(code)

    total = len(codes)
    if limit and limit < total:
        codes = sorted(random.Random(seed).sample(codes, limit))
    if verbose and (dropped or dropped_index):
        parts = [f"{total} 只可用"]
        if dropped_index:
            parts.append(f"{dropped_index} 只指数已剔除（universe.include_index=false）")
        if dropped:
            parts.append(f"{dropped} 只因近 {AVG_DAYS} 日均成交额低于 {threshold / 1e4:,.0f} 万被剔除")
        print("    标的池：" + "，".join(parts))
    return {
        "codes": codes,
        "total": total,
        "dropped_liquidity": dropped,
        "dropped_index": dropped_index,
        "threshold": threshold,
        "sampled": bool(limit and limit < total),
    }


def avg_amount(conn, code: str, days: int = AVG_DAYS) -> float | None:
    """单只标的近 N 个交易日的日均成交额（页面/报告用）。"""
    row = db.query_one(
        conn,
        f"""SELECT AVG(amount) AS adv FROM (
              SELECT amount FROM bars_daily WHERE code=?
              ORDER BY trade_date DESC LIMIT {int(days)}
           )""",
        (code,),
    )
    return float(row["adv"]) if row and row["adv"] is not None else None
