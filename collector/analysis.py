"""AI 指标分析（阿里云百炼 / DashScope）：把已经算好的数字交给模型读一遍。

**这一层不进决策链路。** 状态机、关键带、仓位上限、提醒全部由本地确定性代码算出来，
模型只是一个"旁注"：它读的是同一批数字，说的话不回写、不影响任何结论。
这么定有两个原因：

  1. 真相源只有一个——如果模型的话能改状态分，同一份数据两次运行就会得出两个结论，
     整套审计（factor_contributions / arbitration_log）立刻失去意义；
  2. 模型会编。把它关在"只读旁注"里，编了也只是难看，不会变成一笔错误的交易。

所以这里的每一句话都必须是**可核对**的：
  - 提示词只喂本地已经算出来的数字，不含新闻、不含外部数据；
  - 明确要求"数据不足就直说"，并且**禁止**给目标价、点位预测和买卖建议；
  - 请求与回答原样落库（analysis_log），花了多少 token、跑了多久都记着。

Key 按和推送 token 一样的顺序找：配置文件 → 环境变量 → 本地文件（默认 data/bailian.token，
已被 .gitignore 排除）。**不要**写进 config.yaml，那个文件进版本库。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import db
from .levels import distance_pct, nearest_band

PROVIDER = "bailian"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"

SCOPE_INSTRUMENT = "instrument"
SCOPE_LEDGER = "ledger"
SCOPE_BACKTEST = "backtest"

SYSTEM_PROMPT = (
    "你是一名谨慎的 A 股复盘助手。规则：\n"
    "1. 只使用用户给出的数字，不许引入任何外部行情、新闻、传闻或你的记忆；\n"
    "2. 缺什么就说什么，不许猜。数字不足以判断时明确写「数据不足」；\n"
    "3. 不许给目标价、不许预测点位、不许给买卖建议——那是用户自己决定的；\n"
    "4. 用中文，句子短，不用形容词堆砌，不说「值得关注」这类空话。\n"
    "按这四段输出，每段不超过三句话：\n"
    "① 现在是什么状态（用趋势分和状态持续天数说话）\n"
    "② 价格与关键带的位置关系意味着什么（用给出的百分比，别自己算）\n"
    "③ 风险层的仓位上限与止损是怎么来的、说明了什么\n"
    "④ 这份数据本身有什么不确定的地方（数据质量、样本量、缺项）"
)

LEDGER_SYSTEM_PROMPT = (
    "你是一名量化系统的因子台账审阅人。你看到的是一张表的汇总（每个因子几项统计），"
    "不是原始数据。规则：\n"
    "1. 只用给出的数字说话，不许引入外部信息，不许替它们补算；\n"
    "2. 数字不够就写「样本不足」，不要猜；\n"
    "3. 不给买卖建议，不谈行情；\n"
    "4. 用中文，短句。按三段输出，每段不超过三句话：\n"
    "① 有哪些因子在重复记账（谁和谁相关高、这对打分意味着什么）\n"
    "② 哪些结论现在站得住、哪些只是样本不够（分别点名）\n"
    "③ 影子因子该继续观察还是该退，给一句理由"
)

BACKTEST_SYSTEM_PROMPT = (
    "你是一名量化回测结果审阅人。你看到的是一次参数回测的摘要与排名靠前的几组结果。规则：\n"
    "1. 只用给出的数字，不许自己重算统计量，不许预测未来收益；\n"
    "2. 样本太小、信号数太少就要明说「没有统计意义」，不要硬下结论；\n"
    "3. 不给买卖建议；\n"
    "4. 用中文，短句。按三段输出，每段不超过三句话：\n"
    "① 这次结果值不值得信（看标的数、年数、信号数、可成交性剔除）\n"
    "② 最好那几组是高原还是尖峰（用邻域均值/最差/为正比例说话）\n"
    "③ 现在该不该动配置，为什么"
)


class AnalysisError(RuntimeError):
    """调用链路自己出错（没 key、网络不通、返回不是预期结构）。"""


# ---------------------------------------------------------------- 配置


def settings(cfg: dict) -> dict:
    raw = (cfg.get("analysis") or {}).get("bailian") or {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "model": raw.get("model") or DEFAULT_MODEL,
        "base_url": (raw.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        "token": (raw.get("token") or "").strip(),
        "token_env": raw.get("token_env") or "DASHSCOPE_API_KEY",
        "token_file": raw.get("token_file") or "data/bailian.token",
        "max_tokens": int(raw.get("max_tokens", 900)),
        "temperature": float(raw.get("temperature", 0.2)),
        "timeout": float(raw.get("timeout", 60)),
        "daily_limit": int(raw.get("daily_limit", 20)),
    }


def _project_file(cfg: dict, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(cfg.get("_project_root") or ".") / path
    return path


def resolve_key(cfg: dict) -> tuple[str, str]:
    """返回 (key, 来源说明)。找不到就给空串，由调用方决定怎么办。"""
    conf = settings(cfg)
    if conf["token"]:
        return conf["token"], "config.yaml"
    from_env = (os.environ.get(conf["token_env"]) or "").strip()
    if from_env:
        return from_env, f"环境变量 {conf['token_env']}"
    path = _project_file(cfg, conf["token_file"])
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text.splitlines()[0].strip(), str(path)
    except OSError:
        pass
    return "", ""


# ---------------------------------------------------------------- 取材


def snapshot(conn, cfg: dict, code: str, trade_date: str | None = None) -> dict:
    """把这只标的"已经算出来的数字"整理成一份可核对的事实清单。

    只读，不含任何模型调用——先有清单，再谈怎么问。
    """
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM features_daily WHERE code=?", (code,))
        trade_date = row["d"] if row and row["d"] else None
    if trade_date is None:
        raise AnalysisError(f"{code} 还没有特征数据，先跑一次 3-每日任务")

    feature = db.query_one(conn, "SELECT * FROM features_daily WHERE code=? AND trade_date=?", (code, trade_date))
    if feature is None:
        raise AnalysisError(f"{code} 在 {trade_date} 没有特征数据")
    feature = dict(feature)

    bar = db.query_one(conn, "SELECT * FROM bars_daily WHERE code=? AND trade_date=?", (code, trade_date))
    close = (dict(bar).get("close") if bar else None) or feature.get("close")

    bands = [dict(b) for b in db.query(
        conn, "SELECT * FROM levels WHERE code=? AND trade_date=? ORDER BY price_low", (code, trade_date))]
    support = nearest_band(bands, close, "support") if close else None
    resistance = nearest_band(bands, close, "resistance") if close else None

    contributions = [dict(c) for c in db.query(
        conn,
        """SELECT factor_id, normalized_score, weight, contribution
           FROM factor_contributions WHERE code=? AND trade_date=? AND COALESCE(weight, 0) > 0
           ORDER BY ABS(COALESCE(contribution, 0)) DESC LIMIT 6""",
        (code, trade_date),
    )]
    alerts = [dict(a) for a in db.query(
        conn, "SELECT level, signal_type, message FROM alerts WHERE code=? AND trade_date=? ORDER BY level",
        (code, trade_date))]
    suppressed = [dict(a) for a in db.query(
        conn, "SELECT party_a, rule_applied, suppressed_signal FROM arbitration_log WHERE code=? AND trade_date=?",
        (code, trade_date))]
    # 只挑跟这只标的有关的体检记录：没有冒号的 task 是全局项（breadth 之类），
    # 带冒号的写成 daily:SH588000，别的票的问题不该出现在这只票的分析里。
    health = [
        dict(h)
        for h in db.query(
            conn, "SELECT source, task, status, error_msg FROM data_health WHERE run_date=? AND status!='ok'",
            (trade_date,),
        )
        if ":" not in (h["task"] or "") or code in (h["task"] or "")
    ]
    breadth = db.query_one(conn, "SELECT * FROM market_breadth WHERE trade_date=?", (trade_date,))
    row = db.query_one(conn, "SELECT name, type FROM instruments WHERE code=?", (code,))
    catalog = dict(row) if row else {}

    return {
        "code": code,
        "name": catalog.get("name"),
        "type": catalog.get("type"),
        "trade_date": trade_date,
        "close": close,
        "state": feature.get("state"),
        "state_days": feature.get("state_days"),
        "trend_score": feature.get("trend_score"),
        "opportunity_score": feature.get("opportunity_score"),
        "vol_ratio_20": feature.get("vol_ratio_20"),
        "atr_pct": feature.get("atr_pct"),
        "candle_pattern": feature.get("candle_pattern"),
        "position_cap": feature.get("position_cap"),
        "stop_level": feature.get("stop_level"),
        "risk_reward": feature.get("risk_reward"),
        "risk_note": feature.get("risk_note"),
        "data_quality_flag": feature.get("data_quality_flag"),
        "support": support,
        "resistance": resistance,
        "contributions": contributions,
        "alerts": alerts,
        "suppressed": suppressed,
        "health": health,
        "breadth": dict(breadth) if breadth else None,
    }


def render(snap: dict) -> str:
    """把清单写成模型读得懂的纯文本。数字照抄，不做二次计算。"""
    def num(value, digits: int = 4) -> str:
        """小数位收干净：库里存的是浮点，直接给模型看会出现 0.17825999999999997 这种噪声。"""
        if value is None:
            return "—"
        return f"{round(float(value), digits):g}"

    state_name = {"up": "上升趋势", "down": "下跌趋势", "range": "震荡"}.get(snap.get("state"), snap.get("state"))
    lines = [
        f"标的：{snap.get('code')} {snap.get('name') or ''}（{snap.get('type') or '类型未知'}）",
        f"交易日：{snap.get('trade_date')}，收盘 {snap.get('close')}",
        f"状态：{state_name}，已持续 {snap.get('state_days')} 个交易日；趋势分 {snap.get('trend_score')}（0-100，50 为中性）",
        f"机会分：{snap.get('opportunity_score')}（0-1，0.5 为中性）；量比 {snap.get('vol_ratio_20')}；ATR 占价 {snap.get('atr_pct')}%",
    ]
    if snap.get("candle_pattern"):
        lines.append(f"K 线形态：{snap['candle_pattern']}")
    if snap.get("support"):
        band = snap["support"]
        lines.append(
            f"最近支撑带：{band.get('price_low')}–{band.get('price_high')}"
            f"（距收盘 {distance_pct(band, snap.get('close')):+.2f}%）"
        )
    if snap.get("resistance"):
        band = snap["resistance"]
        lines.append(
            f"最近阻力带：{band.get('price_low')}–{band.get('price_high')}"
            f"（距收盘 {distance_pct(band, snap.get('close')):+.2f}%）"
        )
    cap = snap.get("position_cap")
    lines.append(
        f"风险层：仓位上限 {'0 仓' if not cap else f'{cap:.0%}'}"
        f"，止损位 {snap.get('stop_level')}，盈亏比 {snap.get('risk_reward')}"
    )
    if snap.get("risk_note"):
        lines.append(f"风险层的推导：{snap['risk_note']}")
    if snap.get("contributions"):
        items = [
            f"{row['factor_id']} 贡献 {num(row.get('contribution'))}"
            f"（权重 {num(row.get('weight'), 2)}，归一值 {num(row.get('normalized_score'))}）"
            for row in snap["contributions"]
        ]
        lines.append("因子贡献（越大越主导当日趋势分）：" + "；".join(items))
    if snap.get("alerts"):
        lines.append("当日提醒：" + "；".join(f"[{a['level']}] {a['message']}" for a in snap["alerts"]))
    else:
        lines.append("当日提醒：无（系统默认沉默，没有值得打扰的信号）")
    if snap.get("suppressed"):
        lines.append("被仲裁压制的信号：" + "；".join(
            f"{row.get('party_a')} ← {row.get('rule_applied')}：{row.get('suppressed_signal')}"
            for row in snap["suppressed"]))
    breadth = snap.get("breadth")
    if breadth:
        lines.append(
            f"当日市场广度（本地样本 {breadth.get('coverage')} 只）："
            f"上涨 {breadth.get('up_count')} / 下跌 {breadth.get('down_count')}，"
            f"中位数涨跌 {breadth.get('median_pct_chg')}%、样本越少越不可当全市场看"
        )
    if snap.get("health"):
        lines.append("数据质量：" + "；".join(
            f"{h.get('source')} {h.get('task')} {h.get('status')} {h.get('error_msg') or ''}" for h in snap["health"]))
    lines.append(f"本行数据质量标记：{snap.get('data_quality_flag')}")
    return "\n".join(lines)


def messages(snap: dict) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "以下是本地系统算出来的数字，请按规则解读：\n\n" + render(snap)},
    ]


# ---------------------------------------------------------------- 因子台账


def ledger_snapshot(conn, cfg: dict) -> dict:
    """整张因子台账的**汇总**，不是明细。

    明细（factor_contributions）动辄几万行，喂给模型既贵又没用——
    每只票的每个因子每天都有一条，读它等于读噪声。这里只取每个因子的那几项统计。
    """
    from . import promotion

    items = promotion.factor_stats(conn, cfg)
    row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM factor_contributions")
    return {
        "as_of": row["d"] if row else None,
        "items": [
            {
                "factor_id": item["factor_id"],
                "name": item["name"],
                "layer": item["layer"],
                "category": item["category"],
                "weight": item["weight"],
                "status": item["status"],
                "days": item["days"],
                "ic5": item.get("ic5"),
                "ic20": item.get("ic20"),
                "max_corr": item.get("max_corr"),
                "max_corr_with": item.get("max_corr_with"),
                "verdict": item.get("verdict"),
            }
            for item in items
        ],
        "shadow_days": promotion.SHADOW_DAYS,
        "duplicate_corr": promotion.DUPLICATE_CORR,
    }


def render_ledger(snap: dict) -> str:
    def num(value, digits: int = 3) -> str:
        return "—" if value is None else f"{round(float(value), digits):g}"

    lines = [
        f"因子台账（贡献数据截至 {snap.get('as_of') or '—'}）",
        f"转正门槛：影子运行 ≥ {snap.get('shadow_days')} 个交易日、与现有因子相关系数绝对值 "
        f"< {snap.get('duplicate_corr')}、|20 日 IC| ≥ 0.02",
        "字段顺序：因子ID | 层/类别 | 权重 | 状态 | 覆盖交易日 | IC5 | IC20 | 最大相关 | 系统给的结论",
    ]
    for item in snap["items"]:
        corr = "—"
        if item.get("max_corr") is not None:
            corr = f"{round(float(item['max_corr']), 2):+g}（{item.get('max_corr_with') or ''}）"
        lines.append(
            " | ".join([
                f"{item['factor_id']}（{item['name']}）",
                f"{item['layer']}/{item['category']}",
                "—" if not item["weight"] else f"{item['weight']:g}",
                {"active": "生效", "shadow": "影子"}.get(item["status"], item["status"] or "—"),
                str(item["days"]),
                num(item.get("ic5")),
                num(item.get("ic20")),
                corr,
                item.get("verdict") or "—",
            ])
        )
    return "\n".join(lines)


def ledger_messages(snap: dict) -> list[dict]:
    return [
        {"role": "system", "content": LEDGER_SYSTEM_PROMPT},
        {"role": "user", "content": "这是本地系统算出来的因子台账汇总，请按规则审阅：\n\n" + render_ledger(snap)},
    ]


# ---------------------------------------------------------------- 回测摘要


def backtest_snapshot(cfg: dict, top: int = 5) -> dict:
    """回测结果的**摘要**，不是整张表。

    网格调大之后表可以几百行，全喂进去又贵又让模型抓不住重点。
    真正需要它判断的只有三件事：样本够不够、最好那片是不是高原、当前配置排第几。
    """
    from . import backtest as backtest_mod

    data = backtest_mod.latest_result(cfg["_project_root"])
    if not data:
        raise AnalysisError("还没跑过参数回测，先在回测页点「跑一次回测」")

    rows = data.get("results") or []
    current = data.get("current") or {}
    sample = data.get("sample") or {}

    def slim(row: dict) -> dict:
        near = row.get("plateau") or {}
        return {
            "label": row.get("label"),
            "signals": row.get("signals"),
            "blocked": row.get("blocked"),
            "win20": row.get("win20"),
            "avg20": row.get("avg20"),
            "base20": row.get("base20"),
            "excess20": row.get("excess20"),
            "mdd20": row.get("mdd20"),
            "neighbour_mean": near.get("mean"),
            "neighbour_worst": near.get("worst"),
            "neighbour_positive": near.get("positive"),
        }

    current_rank = None
    for index, row in enumerate(rows, 1):
        params = row.get("params") or {}
        if (params.get("enter_up") == current.get("enter_up")
                and params.get("confirm_days") == current.get("confirm_days")
                and params.get("min_state_days") == current.get("min_state_days")):
            current_rank = index
            break

    return {
        "saved_at": data.get("_saved_at"),
        "code_count": data.get("code_count"),
        "bars": data.get("bars"),
        "years": data.get("years"),
        "cost": data.get("cost"),
        "limit_check": data.get("limit_check"),
        "mode": sample.get("mode"),
        "total": len(rows),
        "positive": sum(1 for row in rows if (row.get("excess20") or 0) > 0),
        "top": [slim(row) for row in rows[:top]],
        "bottom": [slim(row) for row in rows[-top:]] if len(rows) > top else [],
        "current": {k: current.get(k) for k in ("enter_up", "confirm_days", "min_state_days")},
        "current_rank": current_rank,
        "current_row": next((slim(row) for row in rows if row.get("label") == (rows[current_rank - 1].get("label") if current_rank else None)), None),
    }


def render_backtest(snap: dict) -> str:
    def num(value, digits: int = 2) -> str:
        return "—" if value is None else f"{round(float(value), digits):g}"

    lines = [
        f"参数回测（运行于 {snap.get('saved_at') or '—'}）",
        f"样本：{'观察池口径' if snap.get('mode') == 'watchlist' else '全市场抽样'}，"
        f"{snap.get('code_count')} 只标的、{snap.get('bars')} 根日线、约 {num(snap.get('years'), 1)} 年；"
        f"往返成本 {num(snap.get('cost'), 3)}%；"      # cost 本身就是百分数（0.102 = 0.102%）
        f"{'已剔除涨停买不进/跌停卖不出的信号' if snap.get('limit_check') else '未做可成交性检查'}",
        f"共 {snap.get('total')} 组参数，按 20 日净超额排序，其中正超额 {snap.get('positive')} 组",
        "说明：净超额 = 信号平均收益 − 同一批标的随便买的平均收益，已经扣掉同一笔成本",
        "",
        "排名靠前的组合（信号数 | 20日胜率 | 20日均值 | 基准 | 净超额 | 平均回撤 | 邻域均值/最差/为正比例）：",
    ]
    for row in snap["top"]:
        lines.append(
            f"  {row['label']} | {row['signals']} | {num(row['win20'], 1)}% | {num(row['avg20'])}% | "
            f"{num(row['base20'])}% | {num(row['excess20'])}% | {num(row['mdd20'], 1)}% | "
            f"{num(row['neighbour_mean'])} / {num(row['neighbour_worst'])} / {num(row['neighbour_positive'], 0)}%"
        )
    if snap["bottom"]:
        lines.append("")
        lines.append("排名靠后的组合（同上口径）：")
        for row in snap["bottom"]:
            lines.append(
                f"  {row['label']} | {row['signals']} | {num(row['win20'], 1)}% | {num(row['avg20'])}% | "
                f"{num(row['base20'])}% | {num(row['excess20'])}% | {num(row['mdd20'], 1)}% | "
                f"{num(row['neighbour_mean'])} / {num(row['neighbour_worst'])} / {num(row['neighbour_positive'], 0)}%"
            )
    current = snap.get("current") or {}
    lines.append("")
    if snap.get("current_row"):
        row = snap["current_row"]
        lines.append(
            f"当前配置（enter_up={current.get('enter_up')} 确认{current.get('confirm_days')}日 "
            f"最短{current.get('min_state_days')}日）排第 {snap.get('current_rank')} / {snap.get('total')}，"
            f"净超额 {num(row['excess20'])}%、信号 {row['signals']} 次、"
            f"邻域最差 {num(row['neighbour_worst'])}%"
        )
    else:
        lines.append(
            f"当前配置（enter_up={current.get('enter_up')} 确认{current.get('confirm_days')}日 "
            f"最短{current.get('min_state_days')}日）这一组没有出现在结果里"
        )
    return "\n".join(lines)


def backtest_messages(snap: dict) -> list[dict]:
    return [
        {"role": "system", "content": BACKTEST_SYSTEM_PROMPT},
        {"role": "user", "content": "这是一次参数回测的摘要，请按规则审阅：\n\n" + render_backtest(snap)},
    ]


# ---------------------------------------------------------------- 调用


def _post(url: str, payload: dict, key: str, timeout: float = 60.0) -> dict:
    """真正发 HTTP 的地方，单独拆出来是为了测试能替换掉它。"""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise AnalysisError(f"百炼返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise AnalysisError(f"连不上百炼：{exc.reason}") from exc
    except OSError as exc:
        raise AnalysisError(f"连不上百炼：{exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AnalysisError(f"返回不是 JSON：{raw[:120]}") from exc
    if not isinstance(data, dict):
        raise AnalysisError(f"返回格式异常：{raw[:120]}")
    return data


def chat(msgs: list[dict], cfg: dict, key: str | None = None, model: str | None = None) -> dict:
    """发一次对话请求。返回 {ok, text, model, usage, latency_ms, note}，从不抛异常。"""
    conf = settings(cfg)
    key = key or resolve_key(cfg)[0]
    if not key:
        return {"ok": False, "note": f"没有找到 key：写进 {conf['token_file']}，或设置环境变量 {conf['token_env']}"}
    model = model or conf["model"]
    started = time.time()
    try:
        data = _post(
            f"{conf['base_url']}/chat/completions",
            {
                "model": model,
                "messages": msgs,
                "max_tokens": conf["max_tokens"],
                "temperature": conf["temperature"],
            },
            key,
            conf["timeout"],
        )
    except AnalysisError as exc:
        return {"ok": False, "note": str(exc), "model": model,
                "latency_ms": int((time.time() - started) * 1000)}

    choices = data.get("choices") or []
    text = ""
    if choices:
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not text:
        return {"ok": False, "note": f"响应里没有正文：{str(data)[:160]}", "model": model,
                "latency_ms": int((time.time() - started) * 1000)}
    return {
        "ok": True,
        "text": text,
        "model": model,
        "usage": data.get("usage") or {},
        "latency_ms": int((time.time() - started) * 1000),
        "note": "",
    }


# ---------------------------------------------------------------- 落库与读取


def save(conn, snap: dict, msgs: list[dict], result: dict, scope: str = SCOPE_INSTRUMENT) -> int:
    """请求与回答原样留痕：花了多少 token、跑了多久、用的哪个模型都记下来。"""
    usage = result.get("usage") or {}
    row = {
        "created_at": db.now_iso(),
        "scope": scope,
        "trade_date": snap.get("trade_date"),
        "code": snap.get("code"),
        "provider": PROVIDER,
        "model": result.get("model"),
        "prompt": "\n\n".join(f"[{m['role']}] {m['content']}" for m in msgs),
        "answer": result.get("text") or "",
        "prompt_tokens": usage.get("prompt_tokens"),
        "answer_tokens": usage.get("completion_tokens"),
        "latency_ms": result.get("latency_ms"),
        "status": "ok" if result.get("ok") else "failed",
        "error_msg": None if result.get("ok") else (result.get("note") or "")[:500],
    }
    # 这是追加日志，不是主键表：同一天同一只标的跑两次要留下两条，所以直接 INSERT。
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    cursor = conn.execute(
        f"INSERT INTO analysis_log ({columns}) VALUES ({placeholders})", list(row.values())
    )
    conn.commit()
    return cursor.lastrowid or 0


def latest(conn, code: str | None = None, scope: str = SCOPE_INSTRUMENT) -> dict | None:
    """最近一次成功的分析。单只标的按代码取，台账/回测按 scope 取。"""
    if scope == SCOPE_INSTRUMENT:
        # scope 这一列是后加的，老记录是 NULL——按"单只标的"处理，别让它们凭空消失
        row = db.query_one(
            conn,
            """SELECT * FROM analysis_log WHERE COALESCE(scope, ?)=? AND code=? AND provider=? AND status='ok'
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (SCOPE_INSTRUMENT, SCOPE_INSTRUMENT, code, PROVIDER),
        )
    else:
        row = db.query_one(
            conn,
            """SELECT * FROM analysis_log WHERE scope=? AND provider=? AND status='ok'
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (scope, PROVIDER),
        )
    return dict(row) if row else None


def calls_today(conn) -> int:
    """今天已经真的调用过几次（只数真发的，dry-run 和失败的不算）。"""
    row = db.query_one(
        conn,
        "SELECT COUNT(*) AS n FROM analysis_log WHERE provider=? AND substr(created_at, 1, 10)=?",
        (PROVIDER, db.now_iso()[:10]),
    )
    return int(row["n"]) if row else 0


def run(conn, cfg: dict, scope: str, code: str | None = None, trade_date: str | None = None,
        model: str | None = None, dry_run: bool = False, save_result: bool = True,
        force: bool = False) -> dict:
    """统一入口：把某一类东西的数字交给模型读一遍并留痕。

    三种口径（scope）：
      - instrument：单只标的，一次一只，最贵也最有针对性；
      - ledger：整张因子台账的汇总，按需手动跑，一次看全表；
      - backtest：回测结果的摘要（样本 + 前几名 + 当前配置排名），不是整张表。

    返回 {ok, scope, code, trade_date, text, model, usage, latency_ms, note}。
    没开启 / 没 key / 超预算 / 调用失败都只影响这一次调用，绝不改任何本地结论。
    """
    conf = settings(cfg)
    if not conf["enabled"] and not dry_run:
        return {"ok": False, "skipped": True, "note": "AI 分析没开启（analysis.bailian.enabled=false）"}

    # 预算闸门：这东西是按次计费的，绝不能因为"顺手跑一遍"就刷掉一整天。
    # 默认每天 20 次（够看两三只票反复问几轮），0 表示不设上限。
    if not dry_run and not force and conf["daily_limit"] > 0:
        used = calls_today(conn)
        if used >= conf["daily_limit"]:
            return {
                "ok": False,
                "skipped": True,
                "note": (f"今天已经调用过 {used} 次，达到上限 {conf['daily_limit']} 次"
                         f"（analysis.bailian.daily_limit，改配置或加 --force 可绕过）"),
            }

    if scope == SCOPE_LEDGER:
        snap = ledger_snapshot(conn, cfg)
        msgs = ledger_messages(snap)
    elif scope == SCOPE_BACKTEST:
        snap = backtest_snapshot(cfg)
        msgs = backtest_messages(snap)
    else:
        if not code:
            return {"ok": False, "skipped": True, "note": "没指定标的"}
        snap = snapshot(conn, cfg, code, trade_date)
        msgs = messages(snap)

    if dry_run:
        return {
            "ok": True,
            "skipped": True,
            "dry_run": True,
            "scope": scope,
            "code": snap.get("code"),
            "trade_date": snap.get("trade_date"),
            "prompt": "\n\n".join(f"[{m['role']}] {m['content']}" for m in msgs),
            "note": "只演练不发送",
        }

    result = chat(msgs, cfg, model=model)
    if save_result:
        result["saved_id"] = save(conn, snap, msgs, result, scope=scope)
    return {
        "ok": result["ok"],
        "scope": scope,
        "code": snap.get("code"),
        "name": snap.get("name"),
        "trade_date": snap.get("trade_date") or snap.get("as_of"),
        "text": result.get("text") or "",
        "model": result.get("model"),
        "usage": result.get("usage") or {},
        "latency_ms": result.get("latency_ms"),
        "note": result.get("note") or "",
    }


def analyze(conn, cfg: dict, code: str, trade_date: str | None = None,
            model: str | None = None, dry_run: bool = False, save_result: bool = True,
            force: bool = False) -> dict:
    """单只标的的分析（页面与命令行最常用的那条路）。"""
    return run(conn, cfg, SCOPE_INSTRUMENT, code=code, trade_date=trade_date, model=model,
               dry_run=dry_run, save_result=save_result, force=force)
