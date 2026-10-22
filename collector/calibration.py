"""风险层胜率校准：把"回测测出来的净胜率"写回配置。

`risk.win_rate` 原来是手填的 0.5，等于所有信号都被当成抛硬币。这个模块只做一件事：
从已经回填 20 日表现的 P1 买入提醒里，算**扣成本后的净胜率**，给出建议值。

三条纪律：
  1. 只认 P1 买入信号；数据异常、信息类提醒不参与（它们不是交易判断）；
  2. 样本少于 `risk.calibration_min_samples` 时不给建议——30 条以下改全局参数，
     改的通常是噪声；
  3. 默认只打印建议，`--write` 才动 config.yaml，并且自动升 feature_version。
     参数是生产真相源，不能在后台悄悄改。
"""

from __future__ import annotations

import re
from pathlib import Path

from . import costs, db
from .alerts import BUY_SIGNALS

DEFAULT_MIN_SAMPLES = 30


def min_samples(cfg: dict | None = None) -> int:
    value = ((cfg or {}).get("risk") or {}).get("calibration_min_samples")
    return DEFAULT_MIN_SAMPLES if value is None else int(value)


def build(conn, cfg: dict | None = None) -> dict:
    cost = costs.round_trip_pct(cfg)
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT signal_type,
                      COUNT(*) AS n,
                      SUM(CASE WHEN outcome_20d IS NOT NULL THEN 1 ELSE 0 END) AS done,
                      SUM(CASE WHEN outcome_20d > 0 THEN 1 ELSE 0 END) AS gross_wins,
                      SUM(CASE WHEN outcome_20d - ? > 0 THEN 1 ELSE 0 END) AS net_wins,
                      AVG(CASE WHEN outcome_20d IS NOT NULL THEN outcome_20d - ? END) AS avg_net
                 FROM alerts WHERE level='P1'
                 GROUP BY signal_type ORDER BY signal_type""",
            (cost, cost),
        )
    ]
    rows = [row for row in rows if row["signal_type"] in BUY_SIGNALS]
    done = sum(int(row["done"] or 0) for row in rows)
    net_wins = sum(int(row["net_wins"] or 0) for row in rows)
    gross_wins = sum(int(row["gross_wins"] or 0) for row in rows)
    threshold = min_samples(cfg)
    rate = (net_wins / done) if done else None
    return {
        "ok": True,
        "cost": cost,
        "min_samples": threshold,
        "done": done,
        "net_wins": net_wins,
        "gross_wins": gross_wins,
        "net_win_rate": round(rate, 3) if rate is not None else None,
        "gross_win_rate": round(gross_wins / done, 3) if done else None,
        "suggested": round(rate, 3) if (rate is not None and done >= threshold) else None,
        "rows": rows,
    }


def report(result: dict) -> str:
    lines = ["===== 胜率校准 =====",
             f"成本口径：往返 {result['cost']:.3f}%；门槛：至少 {result['min_samples']} 条 P1 样本"]
    if not result["rows"]:
        lines.append("还没有 P1 买入提醒。先跑几天日终，让 alerts 回填 20 日表现。")
        return "\n".join(lines)
    lines.append(f"{'信号':<24}{'回填':>6}{'毛胜率':>8}{'净胜率':>8}{'净均值':>9}")
    for row in result["rows"]:
        done = int(row["done"] or 0)
        gw = f"{row['gross_wins'] / done:.0%}" if done else "—"
        nw = f"{row['net_wins'] / done:.0%}" if done else "—"
        avg = f"{row['avg_net']:+.2f}%" if row["avg_net"] is not None else "—"
        lines.append(f"{row['signal_type']:<24}{done:>6}{gw:>8}{nw:>8}{avg:>9}")
    lines.append("")
    lines.append(f"合计：回填 {result['done']} 条，毛胜率 "
                 f"{(result['gross_win_rate'] or 0):.0%}，净胜率 "
                 f"{(result['net_win_rate'] or 0):.0%}")
    if result["suggested"] is None:
        lines.append(f"样本不足 {result['min_samples']} 条，不给建议——这时改参数改的是噪声。")
    else:
        lines.append(f"建议：risk.win_rate = {result['suggested']}")
        lines.append("确认后执行 python run.py calibrate --write（会自动升 feature_version）；"
                     "不写就只打印，不改生产配置。")
    lines.append("胜率只回答'这个方向有多常赢'，不回答'赢多少'；赔率和成本仍在期望值里。")
    return "\n".join(lines)


def write_win_rate(config_path: str | Path, value: float) -> dict:
    """把 win_rate 写进 config.yaml，并升一个 minor feature_version。

    只改 `risk.win_rate` 这一行（文件里目前只有这一处），不做 YAML 重排——
    重排会把注释和分组顺序弄丢，而这份配置的可读性本身就是设计的一部分。
    """
    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"(?m)^(\s*win_rate:\s*)([0-9]*\.?[0-9]+)\s*(?:#.*)?$")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"config 里找到 {len(matches)} 处 win_rate，拒绝盲改；请手工确认")
    old = matches[0].group(2)
    text = pattern.sub(lambda m: f"{m.group(1)}{value:.3f}", text, count=1)

    version_pattern = re.compile(r'(?m)^(\s*feature_version:\s*"v)(\d+)\.(\d+)(")')
    version = version_pattern.search(text)
    if not version:
        raise ValueError("找不到 project.feature_version，拒绝写入")
    major, minor = int(version.group(2)), int(version.group(3)) + 1
    new_version = f"v{major}.{minor}"
    text = version_pattern.sub(lambda m: f'{m.group(1)}{major}.{minor}{m.group(4)}', text, count=1)
    path.write_text(text, encoding="utf-8")
    return {"old": old, "new": f"{value:.3f}", "version": new_version}
