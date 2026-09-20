"""分档研究：把一个"想法"变成一张表，看它到底有没有用。

为什么要有这个东西：我们讨论过好几轮"回撤到 0.5~0.618 低吸""垃圾小票拖累筛选"
这类判断——全都是**可以先量出来**的。以前每次都要临时写脚本，脚本一删结论就只剩
记忆里的一句话；现在它是命令，随手就能重跑，参数和口径都固定下来。

口径与 22-历史重放 完全一致：信号日收盘确认 → 次日收盘建仓 → 持有 N 个交易日；
"超额"= 该档平均 − **同一天**全样本等权平均（把行情涨跌的影响去掉，
否则测出来的是时机，不是选股）。

刻意只做"从日线就能算出来"的维度（成交额、位置、量能、距支撑、波动），
所以全市场跑一遍只要半分钟。斐波那契回撤那种依赖特征链的维度不进这里——
它要先跑一遍完整特征（几分钟），不值得拖慢日常使用。
"""

from __future__ import annotations

from . import db

DEFAULT_DAYS = 250
DEFAULT_HORIZON = 20

# 每个维度的分档：bins 用 pandas.cut 的语义（左闭右开）
FIELDS: dict[str, dict] = {
    "成交额": {
        "column": "amount60",
        "title": "近 60 日均成交额",
        "bins": [0, 1e7, 3e7, 5e7, 1e8, 3e8, 1e9, 1e13],
        "labels": ["<0.1亿", "0.1-0.3亿", "0.3-0.5亿", "0.5-1亿", "1-3亿", "3-10亿", ">10亿"],
    },
    "位置": {
        "column": "range_pos",
        "title": "在 20 日区间里的位置（0=贴低点，1=贴高点）",
        "bins": [-100, 0.15, 0.3, 0.382, 0.5, 0.618, 0.8, 1.0, 1000],
        "labels": ["0-15%", "15-30%", "30-38.2%", "38.2-50%", "50-61.8%", "61.8-80%",
                   "80-100%", "破前高"],
    },
    "量能": {
        "column": "vol_ratio",
        "title": "今日成交额 ÷ 前 20 日均值",
        "bins": [0, 0.8, 1.2, 1.5, 3, 1000],
        "labels": ["缩量<0.8", "0.8-1.2", "1.2-1.5", "1.5-3", "放量>3"],
    },
    "距支撑": {
        "column": "dist_to_low20",
        "title": "收盘离前 20 日低点多远",
        "bins": [-100, 1, 3, 5, 8, 12, 20, 1000],
        "labels": ["贴住<1%", "1-3%", "3-5%", "5-8%", "8-12%", "12-20%", ">20%"],
    },
    "距关键位": {
        "column": "dist_to_level_abs",
        "title": "离最近的关键位（20 日高低点或 MA60）多远",
        "bins": [0, 0.5, 1, 2, 3, 5, 10, 1000],
        "labels": ["<0.5%", "0.5-1%", "1-2%", "2-3%", "3-5%", "5-10%", ">10%"],
    },
    "波动": {
        "column": "atr_pct",
        "title": "近 14 日日均振幅（%）",
        "bins": [0, 2, 3, 5, 8, 12, 1000],
        "labels": ["<2%", "2-3%", "3-5%", "5-8%", "8-12%", ">12%"],
    },
}

# 常用的两维交叉：看是哪一个维度在起作用，还是必须两个一起
CROSSES = (("位置", "量能"), ("成交额", "位置"), ("成交额", "量能"), ("位置", "波动"))


class ExploreError(RuntimeError):
    """给人看的错误（缺依赖、字段名写错之类）。"""


def _pandas():
    """pandas 是可选依赖（akshare 会带它），没装就明说，不要抛一堆 ImportError。"""
    try:
        import pandas as pd
        import numpy as np
    except ImportError as exc:                       # pragma: no cover - 取决于环境
        raise ExploreError(
            "分档研究要用 pandas：python -m pip install pandas（装了 akshare 的话本来就有）"
        ) from exc
    return pd, np


def load_panel(conn, days: int = DEFAULT_DAYS, horizon: int = DEFAULT_HORIZON, verbose: bool = False):
    """把全市场日线读成一张面板，并算好各维度与前瞻收益。"""
    pd, np = _pandas()
    frame = pd.read_sql_query(
        """SELECT code, trade_date, close, high, low, amount
           FROM bars_daily
           WHERE COALESCE(quality_flag,'ok')!='blocked' AND close > 0
           ORDER BY code, trade_date""",
        conn,
    )
    if frame.empty:
        raise ExploreError("本地还没有日线，先跑 18-全市场同步 或 3-每日任务")
    if verbose:
        print(f"    日线 {len(frame):,} 行，{frame['code'].nunique():,} 只", flush=True)

    grouped = frame.groupby("code", sort=False)
    close = frame["close"]
    frame["ma60"] = grouped["close"].transform(lambda s: s.rolling(60).mean())
    frame["high20"] = grouped["high"].transform(lambda s: s.rolling(20).max().shift(1))
    frame["low20"] = grouped["low"].transform(lambda s: s.rolling(20).min().shift(1))
    frame["amount60"] = grouped["amount"].transform(lambda s: s.rolling(60).mean())
    amount20 = grouped["amount"].transform(lambda s: s.rolling(20).mean().shift(1))
    frame["vol_ratio"] = np.where(amount20 > 0, frame["amount"] / amount20, np.nan)

    frame["dist_to_low20"] = (close - frame["low20"]) / close * 100
    span = frame["high20"] - frame["low20"]
    frame["range_pos"] = np.where(span > 0, (close - frame["low20"]) / span, np.nan)
    # 到最近关键位的距离：20 日高/低点、MA60 里最近的那个（与 features.dist_to_level 同口径）
    # 用 pandas 的逐行最小（自动跳过 NaN），别用 np.nanmin——历史不足时整行都是 NaN，
    # np 会往控制台喷 "All-NaN slice encountered"，看着像出错，其实只是前期样本不可用。
    frame["dist_to_level_abs"] = pd.concat([
        (close - frame["high20"]).abs(),
        (close - frame["low20"]).abs(),
        (close - frame["ma60"]).abs(),
    ], axis=1).min(axis=1) / close * 100
    frame["atr_pct"] = grouped["close"].transform(
        lambda s: s.pct_change().abs().rolling(14).mean()) * 100

    # 前瞻收益：次日收盘建仓，持有 horizon 个交易日
    entry = grouped["close"].shift(-1)
    frame["fwd"] = (grouped["close"].shift(-1 - horizon) / entry - 1) * 100
    frame["fwd5"] = (grouped["close"].shift(-6) / entry - 1) * 100

    dates = sorted(frame["trade_date"].unique())
    if len(dates) > days + horizon + 2:
        keep = set(dates[-days - horizon - 2:-horizon - 2])
        frame = frame[frame["trade_date"].isin(keep)]
    return frame


def bucket_table(frame, field: str, horizon_column: str = "fwd"):
    """某一维度的分档表：样本数、平均、中位、超额、胜率。"""
    pd, _ = _pandas()
    if field not in FIELDS:
        raise ExploreError(f"没有这个维度：{field}（可选 {'、'.join(FIELDS)}）")
    spec = FIELDS[field]
    data = frame[frame[horizon_column].notna() & frame[spec["column"]].notna()].copy()
    if data.empty:
        raise ExploreError(f"{field}：没有可用样本")
    daily = data.groupby("trade_date")[horizon_column].mean()
    data["excess"] = data[horizon_column] - data["trade_date"].map(daily)
    group = pd.cut(data[spec["column"]], bins=spec["bins"], labels=spec["labels"], right=False)
    table = data.groupby(group, observed=False).agg(
        样本=(horizon_column, "size"),
        平均=(horizon_column, "mean"),
        中位=(horizon_column, "median"),
        超额=("excess", "mean"),
        胜率=(horizon_column, lambda s: (s > 0).mean() * 100),
    )
    return table, len(data)


def cross_table(frame, row_field: str, col_field: str, horizon_column: str = "fwd"):
    """两维交叉的平均超额。"""
    pd, _ = _pandas()
    for field in (row_field, col_field):
        if field not in FIELDS:
            raise ExploreError(f"没有这个维度：{field}（可选 {'、'.join(FIELDS)}）")
    rows, cols = FIELDS[row_field], FIELDS[col_field]
    data = frame[frame[horizon_column].notna()
                 & frame[rows["column"]].notna() & frame[cols["column"]].notna()].copy()
    if data.empty:
        raise ExploreError(f"{row_field} × {col_field}：没有可用样本")
    daily = data.groupby("trade_date")[horizon_column].mean()
    data["excess"] = data[horizon_column] - data["trade_date"].map(daily)
    row_group = pd.cut(data[rows["column"]], bins=rows["bins"], labels=rows["labels"], right=False)
    col_group = pd.cut(data[cols["column"]], bins=cols["bins"], labels=cols["labels"], right=False)
    table = data.groupby([row_group, col_group], observed=False)["excess"].agg(["size", "mean"])
    return table, len(data)


def report(frame, fields: list[str] | None = None, crosses: list[tuple[str, str]] | None = None,
           horizon: int = DEFAULT_HORIZON, with_cross: bool = True) -> str:
    lines: list[str] = ["", f"分档研究（持有 {horizon} 个交易日；超额 = 该档平均 − 同一天全样本等权）"]
    for field in (fields or list(FIELDS)):
        table, total = bucket_table(frame, field)
        lines.append("")
        lines.append(f"【{field}】{FIELDS[field]['title']}（{total:,} 个样本）")
        lines.append(f"  {'档':<16}{'样本':>9}{'平均':>9}{'中位':>9}{'超额':>9}{'胜率':>8}")
        for label, row in table.iterrows():
            if not row["样本"]:
                continue
            lines.append(f"  {str(label):<16}{int(row['样本']):>9,}{row['平均']:>8.2f}%"
                         f"{row['中位']:>8.2f}%{row['超额']:>8.2f}%{row['胜率']:>7.0f}%")
    if with_cross:
        for row_field, col_field in (crosses or CROSSES):
            table, total = cross_table(frame, row_field, col_field)
            lines.append("")
            lines.append(f"【{row_field} × {col_field}】平均超额 / 样本数（{total:,} 个样本）")
            labels = FIELDS[col_field]["labels"]
            lines.append("  " + f"{row_field + '↓':<14}"
                         + "".join(f"{str(label):>17}" for label in labels))
            for row_label in FIELDS[row_field]["labels"]:
                cells = []
                for col_label in labels:
                    try:
                        cell = table.loc[(row_label, col_label)]
                        cells.append(f"{cell['mean']:+.2f}%(n={int(cell['size'])})"
                                     if cell["size"] else "—")
                    except KeyError:
                        cells.append("—")
                lines.append("  " + f"{str(row_label):<14}" + "".join(f"{c:>17}" for c in cells))
    lines.append("")
    lines.append("看哪一档的「超额」明显为正，而且**相邻档有梯度**——单调下降的梯度比"
                 "某一格的高数字可信得多（格子越多，偶然好看的格子就越多）。")
    return "\n".join(lines)


def run(conn, cfg: dict, days: int = DEFAULT_DAYS, horizon: int = DEFAULT_HORIZON,
        fields: list[str] | None = None, crosses: list[tuple[str, str]] | None = None,
        with_cross: bool = True, verbose: bool = True) -> dict:
    if verbose:
        print(f"读取全市场日线（回看 {days} 个交易日，持有 {horizon} 日）…", flush=True)
    frame = load_panel(conn, days=days, horizon=horizon, verbose=verbose)
    return {"frame": frame, "text": report(frame, fields=fields, crosses=crosses,
                                           horizon=horizon, with_cross=with_cross)}
