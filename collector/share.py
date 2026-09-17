"""把一只标的画成一张能直接发微信的竖版看板图。

为什么不用 HTML 分享：微信里发 .html 文件，朋友在手机上点开基本打不开；发图最实在。

K 线用 Python 直接生成 SVG（不引入任何绘图库），再由浏览器渲染成 PNG：
  1) Playwright（用系统已装的 Chrome / Edge，或它自带的 chromium）
  2) Chrome / Edge 命令行 headless（Windows 上 Edge 是预装的，macOS 上一般有 Chrome）
  3) 都不可用就把 HTML 卡片留在磁盘上，告诉你手动截图
"""

from __future__ import annotations

import html
import subprocess
from datetime import datetime
from pathlib import Path

from . import db
from .names import display_name
from .server import App

CARD_WIDTH = 1080
CARD_HEIGHT = 1720

UP = "#e2453c"
DOWN = "#12a05c"
GRID = "#ecedf2"
AXIS = "#98a1b0"
MA_COLORS = {"ma20": "#f2a13b", "ma60": "#3b82f6", "ma120": "#8b5cf6"}
BAND_COLORS = {
    "support": ("rgba(59,130,246,.10)", "rgba(59,130,246,.45)"),
    "resistance": ("rgba(226,69,60,.10)", "rgba(226,69,60,.45)"),
}
LEVEL_COLORS = {"P0": "#e2453c", "P1": "#f0a020", "P2": "#8b95a5"}
STATE_TEXT = {"up": "上升趋势", "range": "震荡", "down": "下跌趋势"}

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)


def _fmt(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


# ---------- K 线（SVG） ----------

def svg_chart(bars: list[dict], levels: list[dict], alerts: list[dict],
              width: int = 1000, height: int = 620) -> str:
    """把 K 线画成 SVG 字符串。红涨绿跌，含均线、关键带和提醒标记。"""
    if not bars:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"></svg>'

    pad_l, pad_r, pad_t, pad_b = 6, 84, 10, 30
    vol_h, gap = 92, 14
    price_h = height - pad_t - pad_b - vol_h - gap
    price_top, price_bottom = pad_t, pad_t + price_h
    vol_top, vol_bottom = price_bottom + gap, price_bottom + gap + vol_h
    plot_w = width - pad_l - pad_r
    count = len(bars)

    lows = [b["low"] for b in bars]
    highs = [b["high"] for b in bars]
    lo, hi = min(lows), max(highs)
    for key in MA_COLORS:
        values = [b.get(key) for b in bars if b.get(key) is not None]
        if values:
            lo, hi = min(lo, min(values)), max(hi, max(values))
    for band in levels:
        lo, hi = min(lo, band["price_low"]), max(hi, band["price_high"])
    margin = (hi - lo) * 0.05 or 1
    lo, hi = lo - margin, hi + margin

    xs = lambda i: pad_l + plot_w * (i + 0.5) / count
    y_price = lambda v: price_top + price_h * (1 - (v - lo) / (hi - lo))
    body_w = max(1.2, min(11.0, plot_w / count * 0.66))
    vol_max = max([b.get("volume") or 0 for b in bars] + [1])
    y_vol = lambda v: vol_bottom - vol_h * (v / vol_max)

    parts: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                        f'font-family="-apple-system,PingFang SC,Microsoft YaHei,sans-serif">']

    # 网格与右侧价格轴
    for i in range(5):
        value = lo + (hi - lo) * i / 4
        y = round(y_price(value), 1)
        parts.append(f'<line x1="{pad_l}" y1="{y}" x2="{width - pad_r}" y2="{y}" stroke="{GRID}"/>')
        digits = 1 if value > 100 else 3
        parts.append(f'<text x="{width - pad_r + 6}" y="{y + 4}" font-size="12" fill="{AXIS}">'
                     f'{_fmt(value, digits)}</text>')

    # 关键带
    for band in levels:
        fill, edge = BAND_COLORS.get(band["level_type"], BAND_COLORS["support"])
        y1, y2 = y_price(band["price_high"]), y_price(band["price_low"])
        parts.append(f'<rect x="{pad_l}" y="{y1:.1f}" width="{plot_w}" height="{max(1.5, y2 - y1):.1f}" fill="{fill}"/>')
        parts.append(f'<line x1="{pad_l}" y1="{y1:.1f}" x2="{width - pad_r}" y2="{y1:.1f}" stroke="{edge}" '
                     f'stroke-dasharray="5 4"/>')
        parts.append(f'<line x1="{pad_l}" y1="{y2:.1f}" x2="{width - pad_r}" y2="{y2:.1f}" stroke="{edge}" '
                     f'stroke-dasharray="5 4"/>')

    # 蜡烛
    for index, bar in enumerate(bars):
        x = round(xs(index), 1)
        rising = bar["close"] >= bar["open"]
        color = UP if rising else DOWN
        parts.append(f'<line x1="{x}" y1="{y_price(bar["high"]):.1f}" x2="{x}" y2="{y_price(bar["low"]):.1f}" '
                     f'stroke="{color}" stroke-width="1"/>')
        y_open, y_close = y_price(bar["open"]), y_price(bar["close"])
        top, body_h = min(y_open, y_close), max(1.0, abs(y_close - y_open))
        fill = "#ffffff" if rising else color
        parts.append(f'<rect x="{x - body_w / 2:.1f}" y="{top:.1f}" width="{body_w:.1f}" height="{body_h:.1f}" '
                     f'fill="{fill}" stroke="{color}" stroke-width="1"/>')

    # 均线
    for key, color in MA_COLORS.items():
        points = [(xs(i), y_price(bar[key])) for i, bar in enumerate(bars) if bar.get(key) is not None]
        if len(points) > 1:
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="1.6"/>')

    # 提醒标记
    alerts_by_date: dict[str, list[dict]] = {}
    for alert in alerts:
        alerts_by_date.setdefault(alert["trade_date"], []).append(alert)
    for index, bar in enumerate(bars):
        items = alerts_by_date.get(bar["trade_date"])
        if not items:
            continue
        level = "P0" if any(a["level"] == "P0" for a in items) else (
            "P1" if any(a["level"] == "P1" for a in items) else "P2")
        cy = min(y_price(bar["low"]) + 11, price_bottom - 4)
        parts.append(f'<circle cx="{xs(index):.1f}" cy="{cy:.1f}" r="4" fill="{LEVEL_COLORS[level]}"/>')

    # 成交量
    for index, bar in enumerate(bars):
        rising = bar["close"] >= bar["open"]
        color = "rgba(226,69,60,.55)" if rising else "rgba(18,160,92,.55)"
        y = y_vol(bar.get("volume") or 0)
        parts.append(f'<rect x="{xs(index) - body_w / 2:.1f}" y="{y:.1f}" width="{body_w:.1f}" '
                     f'height="{max(0.5, vol_bottom - y):.1f}" fill="{color}"/>')
    parts.append(f'<line x1="{pad_l}" y1="{vol_bottom}" x2="{width - pad_r}" y2="{vol_bottom}" stroke="{GRID}"/>')
    parts.append(f'<text x="{pad_l + 2}" y="{vol_top + 12}" font-size="12" fill="{AXIS}">成交量</text>')

    # 日期刻度
    ticks = min(6, count)
    for k in range(ticks):
        index = round((count - 1) * k / max(1, ticks - 1))
        x = min(max(xs(index), pad_l + 20), width - pad_r - 20)
        parts.append(f'<text x="{x:.1f}" y="{height - 10}" font-size="12" fill="{AXIS}" '
                     f'text-anchor="middle">{bars[index]["trade_date"][5:]}</text>')

    parts.append("</svg>")
    return "".join(parts)


# ---------- 卡片（HTML） ----------

def build_card(app: App, code: str, days: int = 120, generated_at: str | None = None,
               data: dict | None = None) -> str:
    """生成一张标的看板的 HTML（含内联 SVG，无任何外部资源）。"""
    data = data if data is not None else app.kline(code, days)
    bars = data["bars"]
    last = bars[-1] if bars else None
    name = display_name(code, data.get("name")) or code
    state = (last or {}).get("state")
    state_text = STATE_TEXT.get(state, "数据不足")
    state_color = UP if state == "up" else (DOWN if state == "down" else "#6b7280")
    change_color = UP if (last and (last.get("pct_chg") or 0) > 0) else (
        DOWN if (last and (last.get("pct_chg") or 0) < 0) else "#6b7280")

    # 关键带：按离收盘的距离排序，最多展示 3 条
    bands_html = ""
    if last:
        ordered = sorted(data["levels"], key=lambda b: abs(
            (b["price_low"] + b["price_high"]) / 2 - last["close"]))[:3]
        rows = []
        for band in ordered:
            center = (band["price_low"] + band["price_high"]) / 2
            distance = (last["close"] - center) / last["close"] * 100
            is_support = band["level_type"] == "support"
            label = "支撑" if is_support else "阻力"
            color = "#2f6feb" if is_support else UP
            rows.append(
                f'<div class="bandrow"><span class="tag" style="color:{color};background:{color}1a">{label}</span>'
                f'<span class="range">{_fmt(band["price_low"], 3 if band["price_low"] < 100 else 1)} – '
                f'{_fmt(band["price_high"], 3 if band["price_high"] < 100 else 1)}</span>'
                f'<span class="dist" style="color:{color}">{distance:+.2f}%</span></div>'
            )
        bands_html = "".join(rows) or '<div class="muted">还没有聚出关键带</div>'
    else:
        bands_html = '<div class="muted">还没有数据</div>'

    # 最近提醒
    recent = sorted(data["alerts"], key=lambda a: a["trade_date"], reverse=True)[:3]
    if recent:
        alerts_html = "".join(
            f'<div class="alertrow"><span class="lv lv-{a["level"]}">{a["level"]}</span>'
            f'<span class="date">{a["trade_date"][5:]}</span>'
            f'<span class="msg">{html.escape(str(a["message"] or ""))}</span></div>'
            for a in recent
        )
    else:
        alerts_html = '<div class="muted">最近没有触发提醒</div>'

    # 因子：取贡献最高的三个（只算真正参与打分的）
    factors = [f for f in data["factors"] if f.get("weight")][:3]
    factors_html = " · ".join(
        f'{html.escape(str(f.get("name") or f["factor_id"]))} {_fmt(f.get("raw_value"), 2)}' for f in factors
    ) or "—"

    stamp = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    chart = svg_chart(bars, data["levels"], data["alerts"])
    price = _fmt(last["close"]) if last else "—"
    change = _pct(last["pct_chg"]) if last else "—"
    trade_date = last["trade_date"] if last else "无数据"

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(name)} 看板</title>
<style>
  *{{box-sizing:border-box}}
  html,body{{margin:0;padding:0;width:{CARD_WIDTH}px;background:#fff;
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;color:#1f2430}}
  .card{{width:{CARD_WIDTH}px;height:{CARD_HEIGHT}px;padding:40px 44px 32px;display:flex;flex-direction:column;gap:18px}}
  .top{{display:flex;justify-content:space-between;align-items:baseline;color:#8a93a3;font-size:15px}}
  .top b{{color:#1f2430;font-size:17px;letter-spacing:.5px}}
  .title{{display:flex;align-items:baseline;gap:14px}}
  .title h1{{margin:0;font-size:38px;font-weight:600}}
  .title .code{{color:#8a93a3;font-size:19px;letter-spacing:.5px}}
  .title .state{{margin-left:auto;font-size:16px;font-weight:600;color:{state_color};
    background:{state_color}14;padding:5px 14px;border-radius:99px}}
  .priceline{{display:flex;align-items:baseline;gap:16px}}
  .price{{font-size:62px;font-weight:600;line-height:1;font-variant-numeric:tabular-nums;color:{change_color}}}
  .change{{font-size:26px;font-weight:600;color:{change_color};font-variant-numeric:tabular-nums}}
  .metrics{{display:flex;gap:26px;margin-left:auto;color:#8a93a3;font-size:15px}}
  .metrics b{{display:block;color:#1f2430;font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}}
  .chart{{border:1px solid #eef0f4;border-radius:14px;padding:8px 4px 2px;background:#fff}}
  .sec{{display:flex;gap:18px}}
  .box{{flex:1;border:1px solid #eef0f4;border-radius:14px;padding:14px 16px}}
  .box h3{{margin:0 0 8px;font-size:14px;font-weight:600;color:#8a93a3;letter-spacing:.5px}}
  .bandrow{{display:flex;align-items:center;gap:10px;font-size:17px;padding:4px 0;font-variant-numeric:tabular-nums}}
  .bandrow .tag{{font-size:13px;padding:2px 8px;border-radius:99px}}
  .bandrow .range{{font-weight:600}}
  .bandrow .dist{{margin-left:auto;color:#8a93a3}}
  .alertrow{{display:flex;gap:9px;align-items:baseline;font-size:16px;padding:5px 0}}
  .alertrow .lv{{font-size:12px;font-weight:700;padding:2px 7px;border-radius:99px}}
  .lv-P0{{background:#fdecea;color:{UP}}} .lv-P1{{background:#fff3e0;color:#b26a00}} .lv-P2{{background:#eef0f4;color:#8b95a5}}
  .alertrow .date{{color:#8a93a3;font-variant-numeric:tabular-nums}}
  .alertrow .msg{{flex:1}}
  .muted{{color:#a3aab6;font-size:15px}}
  .factor{{font-size:15px;color:#5b6474}}
  .foot{{margin-top:auto;display:flex;justify-content:space-between;color:#a3aab6;font-size:14px;
    border-top:1px solid #eef0f4;padding-top:14px}}
</style></head>
<body><div class="card">
  <div class="top"><span><b>A 股看板</b> · 数据截至 {trade_date}</span><span>生成于 {stamp}</span></div>
  <div class="title">
    <h1>{html.escape(name)}</h1><span class="code">{html.escape(code)}</span>
    <span class="state">{state_text}{f' · 已持续 {last["state_days"]} 日' if last and last.get("state_days") else ''}</span>
  </div>
  <div class="priceline">
    <span class="price">{price}</span><span class="change">{change}</span>
    <span class="metrics">
      <span>趋势分<b>{_fmt((last or {}).get("trend_score"), 1)}</b></span>
      <span>机会分<b>{_fmt((last or {}).get("opportunity_score"), 2)}</b></span>
      <span>量比<b>{_fmt((last or {}).get("vol_ratio_20"))}</b></span>
    </span>
  </div>
  <div class="chart">{chart}</div>
  <div class="sec">
    <div class="box"><h3>关键带</h3>{bands_html}</div>
    <div class="box"><h3>最近提醒</h3>{alerts_html}</div>
  </div>
  <div class="factor">主要因子贡献：{factors_html}</div>
  <div class="foot"><span>数据来源 baostock · 图为量化规则的机械输出</span><span>仅供参考，不构成投资建议</span></div>
</div></body></html>"""


# ---------- 渲染成 PNG ----------

def _render_playwright(html_path: Path, png_path: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    for channel in ("chrome", "msedge", None):
        try:
            with sync_playwright() as play:
                browser = play.chromium.launch(**({"channel": channel} if channel else {}))
                page = browser.new_page(viewport={"width": CARD_WIDTH, "height": CARD_HEIGHT},
                                        device_scale_factor=2)
                page.goto(html_path.as_uri())
                page.wait_for_timeout(250)
                page.screenshot(path=str(png_path), full_page=True)
                browser.close()
            return True
        except Exception:
            continue
    return False


def _render_chrome_cli(html_path: Path, png_path: Path) -> bool:
    for candidate in CHROME_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            subprocess.run(
                [
                    str(path),
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--force-device-scale-factor=1",
                    f"--window-size={CARD_WIDTH},{CARD_HEIGHT}",
                    f"--screenshot={png_path}",
                    html_path.as_uri(),
                ],
                check=True,
                capture_output=True,
                timeout=90,
            )
            if png_path.exists() and png_path.stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def render_png(html_path: Path, png_path: Path) -> str:
    """HTML → PNG，返回用了哪个渲染器（空串表示都没成功）。"""
    if _render_playwright(html_path, png_path):
        return "playwright"
    if _render_chrome_cli(html_path, png_path):
        return "chrome-headless"
    return ""


def generate(cfg: dict, codes: list[str] | None = None, days: int = 120,
             out_dir: Path | None = None, verbose: bool = True) -> dict:
    """给若干标的生成分享图。返回 {ok, images:[...], skipped:[...], renderer, out_dir}。"""
    app = App(cfg)
    if not codes:
        codes = [item["code"] for item in app.watchlist()]
    target_dir = Path(out_dir) if out_dir else Path(cfg["_project_root"]) / "分享图"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")

    images: list[Path] = []
    skipped: list[str] = []
    renderer = ""
    for code in codes:
        data = app.kline(code, days)
        if not data["bars"]:
            skipped.append(code)
            continue
        card_html = build_card(app, code, days=days, data=data)
        html_path = target_dir / f"{code}-{stamp}.html"
        html_path.write_text(card_html, encoding="utf-8")
        png_path = target_dir / f"{code}-{stamp}.png"
        used = render_png(html_path, png_path)
        renderer = renderer or used
        if used:
            html_path.unlink(missing_ok=True)      # 出图成功就不留中间 HTML
            images.append(png_path)
            if verbose:
                print(f"  {code} → {png_path.name}（{used}）")
        else:
            images.append(html_path)
            if verbose:
                print(f"  {code} → {html_path.name}（没找到浏览器，留在磁盘上手动截图）")
    return {"ok": bool(images), "images": images, "skipped": skipped,
            "renderer": renderer, "out_dir": target_dir}
