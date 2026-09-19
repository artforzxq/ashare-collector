"""推送层：把当天的简报发到手机（PushPlus）。

这一层和别的层一样只有一个出口值：**发没发出去**。
推送失败不改变任何结论——行情已入库、状态已算完，发不出去只意味着
"今天没打扰到你"，不是数据出错。所以这里把所有异常都收敛成返回值，
由调用方决定要不要打印，日终任务永远不会因为推送挂掉。

Token 属于敏感信息，按下面的顺序找，先找到先用：

    1. config.yaml 的 notify.pushplus.token（不推荐：这个文件进版本库）
    2. 环境变量（默认 ASHARE_PUSHPLUS_TOKEN）
    3. 本地文件（默认 data/pushplus.token，已被 .gitignore 排除）

命令行：

    python run.py push              发最近一个交易日的简报
    python run.py push --test       只发一条测试消息，验证 token 通不通
    python run.py push --dry-run    只打印、不真发（看内容长什么样）

同一天默认只推一次：`data/last-push.txt` 记着上次推到哪个交易日，
周末和节假日重跑日终任务时不会把同一份简报再吵你一遍。想重发加 `--force`。
"""

from __future__ import annotations

import json
import os
import platform
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import db

API_URL = "https://www.pushplus.plus/send"
TITLE_PREFIX = "A股简报"


class PushError(RuntimeError):
    """推送链路自己出错（网络、HTTP、返回不是 JSON）。"""


# ---------------------------------------------------------------- 配置


def settings(cfg: dict) -> dict:
    """取出推送配置并补上默认值。"""
    raw = (cfg.get("notify") or {}).get("pushplus") or {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "token": (raw.get("token") or "").strip(),
        "token_env": raw.get("token_env") or "ASHARE_PUSHPLUS_TOKEN",
        "token_file": raw.get("token_file") or "data/pushplus.token",
        "template": raw.get("template") or "txt",
        "topic": (raw.get("topic") or "").strip(),
        "channel": (raw.get("channel") or "").strip(),
        "only_on_alerts": bool(raw.get("only_on_alerts", False)),
        "max_chars": int(raw.get("max_chars", 4000)),
        "timeout": float(raw.get("timeout", 10)),
        "state_file": raw.get("state_file") or "data/last-push.txt",
    }


def _project_file(cfg: dict, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(cfg.get("_project_root") or ".") / path
    return path


def resolve_token(cfg: dict) -> tuple[str, str]:
    """返回 (token, 来源说明)。找不到就给空串，让调用方决定怎么办。"""
    conf = settings(cfg)

    if conf["token"]:
        return conf["token"], "config.yaml"

    env_name = conf["token_env"]
    from_env = (os.environ.get(env_name) or "").strip()
    if from_env:
        return from_env, f"环境变量 {env_name}"

    path = _project_file(cfg, conf["token_file"])
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return _from_file_text(text), str(path)
    except OSError:
        pass

    return "", ""


def _from_file_text(text: str) -> str:
    """文件里可以只写 token，也可以写成 TOKEN=xxx；注释用 # 开头。"""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.+)$", line)
        if match:
            return match.group(1).strip().strip("'\"")
        return line.strip("'\"")
    return ""


def save_token(cfg: dict, token: str) -> Path:
    """把 token 写进本地文件（不进版本库），返回路径。"""
    path = _project_file(cfg, settings(cfg)["token_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------- 内容


def build_message(conn, cfg: dict, trade_date: str | None = None) -> dict:
    """把当天简报整理成一条推送：标题 + 正文。"""
    from . import report as report_mod

    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM features_daily")
        trade_date = row["d"] if row and row["d"] else None
    if trade_date is None:
        raise PushError("库里还没有特征数据，先跑一次 daily 再推送")

    alerts = db.query(conn, "SELECT * FROM alerts WHERE trade_date=? ORDER BY level, code", (trade_date,))
    body = report_mod.daily_report(conn, cfg, trade_date)
    conf = settings(cfg)
    if len(body) > conf["max_chars"]:
        body = body[: conf["max_chars"] - 30].rstrip() + "\n…（内容过长已截断）"

    count = len(alerts)
    title = f"{TITLE_PREFIX} {trade_date}" + (f" · {count} 条提醒" if count else " · 无提醒")
    return {"title": title, "content": body, "trade_date": trade_date, "alerts": count}


# ---------------------------------------------------------------- 发送


def _post(url: str, payload: dict, timeout: float = 10.0) -> dict:
    """真正发 HTTP 请求的地方。单独拆出来是为了测试能替换掉它。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise PushError(f"PushPlus 返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise PushError(f"连不上 PushPlus：{exc.reason}") from exc
    except OSError as exc:
        raise PushError(f"连不上 PushPlus：{exc}") from exc

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise PushError(f"PushPlus 返回的不是 JSON：{raw[:120]}") from exc
    if not isinstance(data, dict):
        raise PushError(f"PushPlus 返回格式异常：{raw[:120]}")
    return data


def send(title: str, content: str, cfg: dict, token: str | None = None) -> dict:
    """发一条消息。返回 {ok, code, msg, note}，从不抛异常。"""
    conf = settings(cfg)
    token = token or resolve_token(cfg)[0]
    if not token:
        return {"ok": False, "code": None, "msg": "", "note": "没有找到 PushPlus token"}

    payload = {"token": token, "title": title, "content": content, "template": conf["template"]}
    if conf["topic"]:
        payload["topic"] = conf["topic"]
    if conf["channel"]:
        payload["channel"] = conf["channel"]

    try:
        data = _post(API_URL, payload, conf["timeout"])
    except PushError as exc:
        return {"ok": False, "code": None, "msg": "", "note": str(exc)}

    code = data.get("code")
    message = str(data.get("msg") or "")
    ok = str(code) == "200"
    return {"ok": ok, "code": code, "msg": message, "note": message or f"code={code}"}


# ---------------------------------------------------------------- 状态


def last_pushed(cfg: dict) -> str:
    path = _project_file(cfg, settings(cfg)["state_file"])
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _mark_pushed(cfg: dict, trade_date: str) -> None:
    path = _project_file(cfg, settings(cfg)["state_file"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{trade_date}\n", encoding="utf-8")
    except OSError:
        pass  # 记不住不是错，最多下次多推一遍


def _mark_alerts_notified(conn, trade_date: str) -> None:
    """给这一天的提醒盖个"已推送"的时间戳，方便事后对账。"""
    try:
        conn.execute(
            "UPDATE alerts SET notified_at=? WHERE trade_date=? AND notified_at IS NULL",
            (db.now_iso(), trade_date),
        )
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------- 对外主入口


def _message_result(message: dict, note: str, ok: bool = True, skipped: bool = True, **extra) -> dict:
    """把简报本身和这一步的结论拼成统一返回，调用方（命令行 / 页面）好直接用。"""
    return {
        "ok": ok,
        "skipped": skipped,
        "note": note,
        "title": message["title"],
        "content": message["content"],
        "trade_date": message["trade_date"],
        "alerts": message["alerts"],
        **extra,
    }


def push_daily(
    conn,
    cfg: dict,
    trade_date: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """把当天简报推给手机。

    返回 {ok, skipped, note, title, trade_date, alerts}。
    `ok=False` 只有一种情况：真的要发、但发送失败了。
    """
    conf = settings(cfg)
    if not conf["enabled"] and not force:
        return {"ok": True, "skipped": True, "note": "推送没开启（notify.pushplus.enabled=false）"}

    message = build_message(conn, cfg, trade_date)
    token, source = resolve_token(cfg)

    # only_on_alerts 是用户定的静默策略，--force 也不越权：它只用来重发/绕过"没开启"，
    # 不该让"今天没提醒"的日子突然响一声。
    if conf["only_on_alerts"] and not message["alerts"]:
        return _message_result(message, "今天没有提醒，按配置保持沉默")

    if not force and last_pushed(cfg) == message["trade_date"]:
        return _message_result(message, f"{message['trade_date']} 已经推过了（要重发加 --force）")

    if dry_run:
        return _message_result(
            message,
            f"只演练不发送（token 来源：{source or '未配置'}）",
            dry_run=True,
        )

    if not token:
        return _message_result(
            message,
            f"没有找到 token：请把 token 写进 {conf['token_file']}，"
            f"或设置环境变量 {conf['token_env']}",
            ok=False,
        )

    result = send(message["title"], message["content"], cfg, token)
    if result["ok"]:
        _mark_pushed(cfg, message["trade_date"])
        _mark_alerts_notified(conn, message["trade_date"])

    return _message_result(
        message,
        f"已推送（token 来源：{source}）" if result["ok"] else f"推送失败：{result['note']}",
        ok=result["ok"],
        skipped=False,
        code=result["code"],
    )


def send_test(cfg: dict, dry_run: bool = False) -> dict:
    """发一条测试消息：只回答"token 和网络通不通"。"""
    token, source = resolve_token(cfg)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"{TITLE_PREFIX} · 推送测试"
    content = (
        f"这是一条测试消息，说明推送链路已经打通。\n\n"
        f"时间：{now}\n"
        f"token 来源：{source or '未配置'}\n"
        f"机器：{platform.node() or 'unknown'}\n\n"
        "正式消息在每个交易日收盘后自动发送。"
    )
    if dry_run:
        return {"ok": True, "skipped": True, "dry_run": True, "note": f"只演练不发送（token 来源：{source or '未配置'}）", "title": title, "content": content}
    if not token:
        return {"ok": False, "skipped": True, "note": "没有找到 PushPlus token", "title": title, "content": content}
    result = send(title, content, cfg, token)
    return {
        "ok": result["ok"],
        "skipped": False,
        "note": f"测试消息已发到手机（token 来源：{source}）" if result["ok"] else f"发送失败：{result['note']}",
        "code": result["code"],
        "title": title,
        "content": content,
    }
