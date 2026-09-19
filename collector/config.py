"""配置加载与默认值合并。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULTS: Dict[str, Any] = {
    "project": {
        "name": "ashare-collector",
        "spec_version": "1.1",
        "db_path": "data/market.db",
        "timezone": "Asia/Shanghai",
    },
    "watchlist": {"indices": [], "etfs": [], "stocks": []},
    "sources": {
        "primary": "fixture",
        "backup": "fixture_alt",
        "fallback": "baostock",
        "extra": [],
        # 全市场代码表按这个顺序并起来（覆盖面见 warehouse.directory_sources）
        "directory": ["sina", "akshare", "baostock"],
        "min_interval_sec": 0.5,
    },
    "collection": {
        "daily_after": "15:30",
        "intraday_interval_min": 5,
        "tail_boost_after": "14:30",
        "intraday_period": 30,
        "keep_intraday_days": 250,
        "factor_keep_days": 500,
    },
    "validation": {
        "price_tol": 0.003,
        "amount_tol": 0.03,
        "jump_pct_limit": 11.0,
        "volume_anomaly_ratio": 10.0,
        "critical_codes": [],
    },
    "state": {
        "enter_up": 70,
        "exit_up": 55,
        "enter_down": 30,
        "exit_down": 45,
        "confirm_days": 2,
        "min_state_days": 3,
        "neutral_band": 0.10,
    },
    "levels": {"bins": 30, "top_bins": 6, "merge_gap_pct": 0.5, "max_levels": 4},
    "alerts": {
        "budget_p1": 3,
        "cooldown_minutes_p0": 15,
        "cooldown_days_p1": 1,
        "cooldown_days_p2": 5,
    },
    # 手机推送：token 不写在这里（这个文件进版本库），见 collector/notify.py
    "notify": {
        "pushplus": {
            "enabled": False,
            "token": "",
            "token_env": "ASHARE_PUSHPLUS_TOKEN",
            "token_file": "data/pushplus.token",
            "template": "txt",
            "topic": "",
            "channel": "",
            "only_on_alerts": False,
            "max_chars": 4000,
            "timeout": 10,
            "state_file": "data/last-push.txt",
        }
    },
    "warehouse": {
        "snapshot_daily": True,
        "history_days": 750,
        "batch_size": 800,
    },
    "risk": {
        "base_cap": {"up": 0.8, "range": 0.4, "down": 0.0},
        "probe_cap": 0.3,
        "target_atr_pct": 2.0,
        "stop_buffer_pct": 0.5,
        "atr_stop_multiple": 2.0,
        "max_stop_pct": 8.0,
        "win_rate": 0.5,
        "target_expectancy": 0.5,
        "open_space_high_tolerance_pct": 8.0,
        "open_space_atr_multiple": 3.0,
        "no_resistance_rr": 1.0,
        "cap_floor": 0.3,
    },
    "screen": {"min_bars": 80, "top": 30, "criteria": []},
    "factors": [],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None, project_root: str | Path | None = None) -> Dict[str, Any]:
    """读取配置并与默认值合并；db_path 解析为绝对路径（相对 config 所在目录）。"""
    env_path = os.environ.get("ASHARE_CONFIG")
    cfg_path = Path(path or env_path or "config.yaml").resolve()
    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{cfg_path}")

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg = _deep_merge(DEFAULTS, raw)

    root = Path(project_root).resolve() if project_root else cfg_path.parent
    cfg["_config_path"] = str(cfg_path)
    cfg["_project_root"] = str(root)

    db_path = Path(cfg["project"]["db_path"])
    if not db_path.is_absolute():
        db_path = root / db_path
    cfg["_db_path"] = str(db_path)
    return cfg


def use_fixture_sources(cfg: Dict[str, Any], end_date: str | None = None) -> Dict[str, Any]:
    """把数据源强制切回离线夹具。

    自检与单元测试必须走这条路：它们要验证的是链路和状态机，
    不该因为配置里换成了真实源就去联网，否则一次跑几分钟，断网还会直接失败。
    """
    sources = cfg.setdefault("sources", {})
    sources["primary"] = "fixture"
    sources["backup"] = "fixture_alt"
    if end_date:
        cfg["_fixture_end_date"] = end_date
    return cfg


def watchlist_codes(cfg: Dict[str, Any]) -> list[dict]:
    """展开观察池，返回 [{code, type, role}]，去重且保持配置顺序。"""
    items: list[dict] = []
    seen: set[str] = set()

    def add(codes, kind, role):
        for code in codes or []:
            if code in seen:
                continue
            seen.add(code)
            items.append({"code": code, "type": kind, "role": role})

    add(cfg["watchlist"].get("indices"), "index", "基准")
    add(cfg["watchlist"].get("etfs"), "etf", "观察")
    add(cfg["watchlist"].get("stocks"), "stock", "持仓")
    return items
