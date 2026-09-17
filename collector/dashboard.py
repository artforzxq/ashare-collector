"""简报页面生成器：把数据库里的状态、关键带、提醒渲染成单文件 HTML。

不依赖任何外部资源（无 CDN、无 JS 框架），双击就能看，也方便直接发给人看。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import db
from .levels import distance_pct, nearest_band

STATE_LABEL = {"up": "上升趋势", "range": "震荡", "down": "下跌趋势"}
LEVEL_LABEL = {"P0": "立即处理", "P1": "可操作", "P2": "信息"}
RULE_LABEL = {
    "R0_DATA_HEALTH": "数据健康冻结",
    "R1_RISK_VETO": "风险否决",
    "R2_STATE_PRIORITY": "状态优先",
    "R3_NEUTRAL_SILENCE": "不确定，默认沉默",
    "R4_CROSS_PERIOD": "跨周期以日线为准",
    "R5_COOLDOWN": "冷却期内重复触发",
    "R6_BUDGET": "超出当日预算",
}


def _price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if abs(value) >= 100 else f"{value:.3f}"


def _num(value) -> str:
    return "—" if value is None else f"{value:g}"


def gather(conn, cfg: dict, trade_date: str | None = None) -> dict:
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM features_daily")
        trade_date = row["d"] if row and row["d"] else None
    if not trade_date:
        raise SystemExit("数据库里还没有特征数据，先跑一次 daily。")

    breadth = db.query_one(conn, "SELECT * FROM market_breadth WHERE trade_date=?", (trade_date,))
    alerts = db.query(conn, "SELECT * FROM alerts WHERE trade_date=? ORDER BY level, code", (trade_date,))
    suppressed = db.query(conn, "SELECT * FROM arbitration_log WHERE trade_date=? ORDER BY id", (trade_date,))
    health = db.query(conn, "SELECT * FROM data_health WHERE run_date=? ORDER BY task", (trade_date,))

    instruments = []
    for row in db.query(conn, "SELECT * FROM features_daily WHERE trade_date=? ORDER BY code", (trade_date,)):
        code = row["code"]
        history = db.query(
            conn,
            "SELECT trade_date, trend_score, state FROM features_daily WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 60",
            (code, trade_date),
        )
        history = [dict(item) for item in reversed(history)]
        close_row = db.query_one(conn, "SELECT close FROM bars_daily WHERE code=? AND trade_date=?", (code, trade_date))
        close = close_row["close"] if close_row else None
        bands = [
            dict(item)
            for item in db.query(conn, "SELECT * FROM levels WHERE code=? AND trade_date=? ORDER BY price_low", (code, trade_date))
        ]
        instruments.append(
            {
                "code": code,
                "state": row["state"],
                "state_days": row["state_days"],
                "trend_score": row["trend_score"],
                "opportunity_score": row["opportunity_score"],
                "quality": row["data_quality_flag"],
                "consolidation_days": row["consolidation_days"],
                "vol_shrink_ratio": row["vol_shrink_ratio"],
                "breakout_confirmed": row["breakout_confirmed"],
                "close": close,
                "ma60": row["ma60"],
                "support": nearest_band(bands, close, "support") if close else None,
                "resistance": nearest_band(bands, close, "resistance") if close else None,
                "history": history,
                "alerts": [dict(a) for a in alerts if a["code"] == code],
            }
        )

    return {
        "trade_date": trade_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": cfg["sources"]["primary"],
        "feature_version": str(cfg["project"].get("feature_version", "v1")),
        "breadth": dict(breadth) if breadth else None,
        "instruments": instruments,
        "alerts": [dict(a) for a in alerts],
        "suppressed": [dict(s) for s in suppressed],
        "health": [dict(h) for h in health],
    }


def _score_chart(history: list[dict], width: int = 300, height: int = 74) -> str:
    """趋势分走势：0–100 纵轴，标出迟滞阈值带，底部一条状态色带。"""
    if not history:
        return ""
    pad_left, pad_right, pad_top = 26, 8, 6
    plot_h = height - 30
    step = (width - pad_left - pad_right) / max(1, len(history) - 1)

    def y(score: float) -> float:
        return pad_top + (100 - score) / 100 * plot_h

    def x(index: int) -> float:
        return pad_left + index * step

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="趋势分走势">']
    parts.append(f'<rect x="{pad_left}" y="{y(100):.1f}" width="{width - pad_left - pad_right}" height="{y(70) - y(100):.1f}" fill="var(--up)" opacity="0.10"/>')
    parts.append(f'<rect x="{pad_left}" y="{y(70):.1f}" width="{width - pad_left - pad_right}" height="{y(55) - y(70):.1f}" fill="var(--flat)" opacity="0.18"/>')
    parts.append(f'<rect x="{pad_left}" y="{y(30):.1f}" width="{width - pad_left - pad_right}" height="{y(0) - y(30):.1f}" fill="var(--down)" opacity="0.10"/>')

    for score in (70, 55, 30):
        parts.append(
            f'<line x1="{pad_left}" y1="{y(score):.1f}" x2="{width - pad_right}" y2="{y(score):.1f}" stroke="var(--line)" stroke-width="1"/>'
        )

    points = [(x(i), y(h["trend_score"])) for i, h in enumerate(history) if h["trend_score"] is not None]
    if len(points) >= 2:
        path = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        parts.append(f'<polyline points="{path}" fill="none" stroke="var(--accent)" stroke-width="1.8"/>')
        last_x, last_y = points[-1]
        parts.append(f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="var(--accent)"/>')
        parts.append(
            f'<text x="{last_x:.1f}" y="{max(12, last_y - 7):.1f}" font-size="10" text-anchor="end" fill="var(--fg)">'
            f'{history[-1]["trend_score"]:.0f}</text>'
        )

    lane_y = height - 16
    run_start = 0
    for index in range(1, len(history) + 1):
        if index == len(history) or history[index]["state"] != history[run_start]["state"]:
            color = {"up": "var(--up)", "range": "var(--flat)", "down": "var(--down)"}[history[run_start]["state"]]
            x0 = x(run_start) if run_start else pad_left
            x1 = x(index - 1) + step if index < len(history) else width - pad_right
            parts.append(
                f'<rect x="{x0:.1f}" y="{lane_y}" width="{max(1.0, x1 - x0):.1f}" height="7" fill="{color}" opacity="0.75"/>'
            )
            run_start = index

    for score in (100, 50, 0):
        parts.append(
            f'<text x="{pad_left - 4}" y="{y(score) + 3:.1f}" font-size="9" text-anchor="end" fill="var(--muted)">{score}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _fmt_distance(band: dict | None, close: float | None) -> str:
    if not band or not close:
        return "—"
    value = distance_pct(band, close)
    return f"{value:+.2f}%"


def _instrument_card(item: dict) -> str:
    state = item["state"] or "range"
    score = item["trend_score"]
    score_text = f"{score:.0f}" if score is not None else "—"
    quality_note = "" if item["quality"] in (None, "ok") else f'<span class="warn">数据 {item["quality"]}，已冻结提醒</span>'
    days = item["consolidation_days"] or 0
    shrink = item["vol_shrink_ratio"]
    shrink_text = f"量能 {shrink:.2f} 倍" if shrink else "量能—"
    near_high = bool(item["close"] and item["ma60"] and item["close"] > item["ma60"] * 1.02)
    shelf = ""
    if 20 <= days <= 60 and near_high and shrink is not None and shrink <= 0.6 and state != "down":
        shelf = f'<span class="shelf">蓄势待发 · 横盘 {days} 日，{shrink_text}</span>'
    elif days >= 20:
        shelf = f'<div class="row"><span>窄幅整理</span><b>{days} 日</b><i>{shrink_text}</i></div>'
    breakout = '<span class="shelf">放量突破确认</span>' if item["breakout_confirmed"] else ""

    rows = [
        f'<div class="row"><span>支撑带</span><b>{_price(item["support"]["price_low"])}–{_price(item["support"]["price_high"])}</b><i>{_fmt_distance(item["support"], item["close"])}</i></div>'
        if item["support"]
        else "",
        f'<div class="row"><span>阻力带</span><b>{_price(item["resistance"]["price_low"])}–{_price(item["resistance"]["price_high"])}</b><i>{_fmt_distance(item["resistance"], item["close"])}</i></div>'
        if item["resistance"]
        else "",
    ]

    alerts = "".join(
        f'<li class="alert-{a["level"]}"><b>[{a["level"]}]</b> {a["message"]}</li>' for a in item["alerts"]
    )

    return f"""
    <article class="card state-{state}">
      <header>
        <div>
          <h3>{item["code"]}</h3>
          <p class="sub">收盘 {_price(item["close"])} · 机会分 {item["opportunity_score"]}</p>
        </div>
        <div class="chip">{STATE_LABEL.get(state, state)}</div>
      </header>
      <div class="score"><b>{score_text}</b><span>趋势分 · 已持续 {item["state_days"] or 0} 个交易日</span></div>
      {_score_chart(item["history"])}
      <div class="rows">{"".join(rows)}{shelf}{breakout}</div>
      {f'<ul class="alerts">{alerts}</ul>' if alerts else ""}
      {quality_note}
    </article>
    """


def render(data: dict) -> str:
    breadth = data["breadth"] or {}
    up, down = breadth.get("up_count"), breadth.get("down_count")
    total = (up or 0) + (down or 0)
    up_ratio = (up or 0) / total * 100 if total else 50
    limit_up = breadth.get("limit_up_count")
    median = breadth.get("median_pct_chg")
    amount = breadth.get("total_amount")

    alert_items = "".join(
        f'<li class="alert-{a["level"]}"><span class="badge">{a["level"]} · {LEVEL_LABEL.get(a["level"], "")}</span>'
        f'<div><b>{a["code"]} {a["signal_type"]}</b><p>{a["message"]}</p></div></li>'
        for a in data["alerts"]
    ) or '<li class="empty">今日无提醒</li>'

    suppressed_items = "".join(
        f'<li><b>{s["code"]} {s["party_a"]}</b><span>{RULE_LABEL.get(s["rule_applied"], s["rule_applied"])}'
        f'（{s["rule_applied"]}） · {s["suppressed_signal"]}</span></li>'
        for s in data["suppressed"]
    ) or '<li class="empty">无被压制的信号</li>'

    health_rows = "".join(
        f'<tr><td>{h["task"]}</td><td>{h["source"]}</td><td>{h["status"]}</td><td>{h["rows"] or 0}</td>'
        f'<td>{h["error_msg"] or ""}</td></tr>'
        for h in data["health"]
    ) or '<tr><td colspan="5">无记录</td></tr>'

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>交易简报 {data["trade_date"]}</title>
<style>
  :root {{
    --bg: #f6f7f9; --panel: #ffffff; --fg: #1b2430; --muted: #6b7684; --line: #e3e7ed;
    --up: #d0453f; --down: #2e9569; --flat: #8a94a6; --accent: #2f6fd0;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14181d; --panel: #1c2229; --fg: #e8ecf1; --muted: #9aa5b1; --line: #2c343d;
      --up: #e2685f; --down: #46b183; --flat: #7d8794; --accent: #6fa2e8;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 26px 22px 60px; background: var(--bg); color: var(--fg);
    font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; line-height: 1.6;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 21px; margin: 0 0 4px; }}
  h2 {{ font-size: 14px; margin: 26px 0 10px; color: var(--muted); font-weight: 500; }}
  .meta {{ color: var(--muted); font-size: 12.5px; margin-bottom: 18px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
  .stat {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; }}
  .stat span {{ display: block; color: var(--muted); font-size: 12px; }}
  .stat b {{ font-size: 19px; font-variant-numeric: tabular-nums; }}
  .bar {{ height: 7px; border-radius: 4px; background: var(--down); overflow: hidden; margin-top: 8px; }}
  .bar i {{ display: block; height: 100%; background: var(--up); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 12px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--flat); border-radius: 10px; padding: 12px 14px; }}
  .card.state-up {{ border-left-color: var(--up); }}
  .card.state-down {{ border-left-color: var(--down); }}
  .card header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
  .card h3 {{ margin: 0; font-size: 15px; letter-spacing: .3px; }}
  .sub {{ margin: 2px 0 0; color: var(--muted); font-size: 12px; }}
  .chip {{ font-size: 12px; padding: 2px 9px; border-radius: 20px; border: 1px solid var(--line); white-space: nowrap; }}
  .state-up .chip {{ color: var(--up); border-color: var(--up); }}
  .state-down .chip {{ color: var(--down); border-color: var(--down); }}
  .score {{ display: flex; align-items: baseline; gap: 8px; margin: 8px 0 2px; }}
  .score b {{ font-size: 26px; font-variant-numeric: tabular-nums; }}
  .score span {{ color: var(--muted); font-size: 12px; }}
  .rows {{ display: grid; gap: 4px; margin-top: 8px; font-size: 12.5px; }}
  .row {{ display: flex; justify-content: space-between; gap: 8px; }}
  .row span {{ color: var(--muted); }}
  .row i {{ color: var(--muted); font-style: normal; font-variant-numeric: tabular-nums; }}
  .shelf {{ color: var(--accent); font-size: 12.5px; }}
  .warn {{ display: block; margin-top: 8px; color: var(--up); font-size: 12.5px; }}
  ul {{ list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 7px; }}
  .alerts li, #alerts li {{ display: flex; gap: 10px; align-items: flex-start; border-left: 3px solid var(--flat); padding: 7px 10px; background: var(--bg); border-radius: 0 7px 7px 0; font-size: 13px; }}
  .alert-P0 {{ border-left-color: var(--up) !important; }}
  .alert-P1 {{ border-left-color: var(--accent) !important; }}
  .badge {{ font-size: 11.5px; color: var(--muted); white-space: nowrap; }}
  #alerts p {{ margin: 2px 0 0; color: var(--muted); font-size: 12.5px; }}
  .suppressed li {{ display: flex; justify-content: space-between; gap: 12px; font-size: 12.5px; color: var(--muted); border-bottom: 1px dashed var(--line); padding: 6px 0; }}
  .empty {{ color: var(--muted); font-size: 12.5px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
  th {{ color: var(--muted); font-weight: 500; }}
  footer {{ margin-top: 26px; color: var(--muted); font-size: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>交易简报 · {data["trade_date"]}</h1>
  <p class="meta">数据源 {data["source"]} · 规则版本 {data["feature_version"]} · 生成于 {data["generated_at"]}</p>

  <div class="stats">
    <div class="stat"><span>上涨 / 下跌</span><b>{_num(up)} / {_num(down)}</b>
      <div class="bar"><i style="width:{up_ratio:.0f}%"></i></div></div>
    <div class="stat"><span>涨停家数</span><b>{_num(limit_up)}</b></div>
    <div class="stat"><span>涨跌幅中位数</span><b>{"—" if median is None else f"{median}%"}</b></div>
    <div class="stat"><span>全市场成交额</span><b>{"—" if amount is None else f"{amount / 1e8:.0f} 亿"}</b></div>
  </div>

  <h2>标的与状态</h2>
  <div class="grid">{"".join(_instrument_card(item) for item in data["instruments"])}</div>

  <h2>今日提醒</h2>
  <ul id="alerts" class="panel">{alert_items}</ul>

  <h2>被仲裁压制的信号（沉默或降级）</h2>
  <ul class="suppressed panel">{suppressed_items}</ul>

  <h2>数据体检</h2>
  <div class="panel"><table>
    <thead><tr><th>任务</th><th>数据源</th><th>状态</th><th>记录数</th><th>说明</th></tr></thead>
    <tbody>{health_rows}</tbody>
  </table></div>

  <footer>页面由 collector/dashboard.py 生成，数据来自本地 SQLite，无外部依赖。</footer>
</div>
</body>
</html>
"""


def write(conn, cfg: dict, trade_date: str | None = None, out_path: str | Path | None = None) -> Path:
    data = gather(conn, cfg, trade_date)
    target = Path(out_path) if out_path else Path(cfg["_project_root"]) / "dashboard.html"
    target.write_text(render(data), encoding="utf-8")
    return target
