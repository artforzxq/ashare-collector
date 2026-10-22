"""券商成交 CSV 导入与 FIFO 对账。

不绑定某一家券商：券商导出的列名差异很大，这里用别名表归一成
date / code / side / price / quantity / fees，再按 FIFO 配对。

边界（必须说明）：
  - 只做成交与已实现盈亏，不处理分红、送转、拆股、配股；这些需要人工确认；
  - 不自动改 `trades` 账本。券商成交是事实，账本里的论点是判断，两者不能混；
  - 同一笔成交重复导入会被唯一键挡掉；部分成交若日期/代码/方向/价格/数量完全相同，
    也应合并成一行再导入。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import db
from .industries import normalize_code

ALIASES = {
    "成交日期": "trade_date", "日期": "trade_date", "发生日期": "trade_date",
    "date": "trade_date", "trade_date": "trade_date",
    "证券代码": "code", "代码": "code", "股票代码": "code", "code": "code",
    "证券名称": "name", "名称": "name", "股票名称": "name", "name": "name",
    "买卖标志": "side", "操作": "side", "买卖": "side", "成交类型": "side",
    "side": "side", "direction": "side",
    "成交价格": "price", "成交价": "price", "价格": "price", "price": "price",
    "成交数量": "quantity", "数量": "quantity", "quantity": "quantity", "qty": "quantity",
    "成交金额": "amount", "金额": "amount", "amount": "amount",
    "佣金": "commission", "手续费": "commission", "commission": "commission",
    "印花税": "stamp_tax", "stamp_tax": "stamp_tax",
    "过户费": "transfer_fee", "transfer_fee": "transfer_fee",
    "其他费用": "other_fee", "其他费": "other_fee", "other_fee": "other_fee",
}

BUY_WORDS = {"买入", "买", "证券买入", "融资买入", "buy", "b"}
SELL_WORDS = {"卖出", "卖", "证券卖出", "融券卖出", "sell", "s"}


class BrokerError(ValueError):
    """CSV 格式或字段不可识别。"""


def _number(value, default=0.0) -> float:
    text = str(value or "").replace(",", "").replace("¥", "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise BrokerError(f"不是数字：{value}") from exc


def _date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise BrokerError("成交日期为空")
    text = text.replace("/", "-").split(" ")[0]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _side(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in BUY_WORDS:
        return "buy"
    if text in SELL_WORDS:
        return "sell"
    raise BrokerError(f"认不出买卖方向：{value}")


def _read(path: str | Path) -> list[dict]:
    target = Path(path).expanduser()
    if not target.exists():
        raise BrokerError(f"找不到文件：{target}")
    try:
        text = target.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = target.read_text(encoding="gb18030")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise BrokerError("CSV 是空的")
    reader = csv.DictReader(lines)
    headers = reader.fieldnames or []
    mapped = {}
    for header in headers:
        key = ALIASES.get((header or "").strip().lower()) or ALIASES.get((header or "").strip())
        if key:
            mapped[header] = key
    for required in ("trade_date", "code", "side", "price", "quantity"):
        if required not in mapped.values():
            raise BrokerError(f"缺少必需列：{required}（可用别名见 collector/broker.py）")
    rows = []
    for raw in reader:
        item = {mapped[key]: value for key, value in raw.items() if key in mapped}
        price = _number(item.get("price"))
        quantity = _number(item.get("quantity"))
        if price <= 0 or quantity <= 0:
            continue
        code = normalize_code(item.get("code", ""))
        if not code:
            continue
        fees = sum(_number(item.get(key)) for key in ("commission", "stamp_tax", "transfer_fee", "other_fee"))
        rows.append({
            "trade_date": _date(item.get("trade_date")),
            "code": code,
            "name": (item.get("name") or "").strip() or None,
            "side": _side(item.get("side")),
            "price": price,
            "quantity": quantity,
            "amount": _number(item.get("amount"), price * quantity) or price * quantity,
            "commission": _number(item.get("commission")),
            "stamp_tax": _number(item.get("stamp_tax")),
            "transfer_fee": _number(item.get("transfer_fee")),
            "other_fee": _number(item.get("other_fee")),
            "fee_total": round(fees, 2),
            "raw_json": db.dump_json(raw),
        })
    if not rows:
        raise BrokerError("没有可导入的成交行（检查代码/价格/数量）")
    return rows


def import_csv(conn, path: str | Path, broker: str | None = None) -> dict:
    rows = _read(path)
    before = db.query_one(conn, "SELECT COUNT(*) AS n FROM broker_executions")["n"]
    for row in rows:
        row["broker"] = broker
        row["created_at"] = db.now_iso()
    written = db.upsert_rows(
        conn, "broker_executions", rows,
        ["trade_date", "code", "side", "price", "quantity"])
    after = db.query_one(conn, "SELECT COUNT(*) AS n FROM broker_executions")["n"]
    return {"ok": True, "rows": len(rows), "written": written,
            "new": int(after) - int(before), "broker": broker}


def fifo(conn) -> dict:
    """按 FIFO 配对买卖，返回未平仓、已实现盈亏和异常。"""
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT * FROM broker_executions
                ORDER BY trade_date, code, id""",
        )
    ]
    lots: dict[str, list[dict]] = {}
    matches = []
    unmatched = []
    for row in rows:
        code = row["code"]
        if row["side"] == "buy":
            unit_fee = (row["fee_total"] or 0) / row["quantity"]
            lots.setdefault(code, []).append({
                "date": row["trade_date"], "quantity": row["quantity"],
                "price": row["price"], "unit_fee": unit_fee,
            })
            continue
        remaining = row["quantity"]
        unit_sell_fee = (row["fee_total"] or 0) / row["quantity"]
        while remaining > 1e-9 and lots.get(code):
            lot = lots[code][0]
            qty = min(remaining, lot["quantity"])
            gross = qty * (row["price"] - lot["price"])
            fees = qty * lot["unit_fee"] + qty * unit_sell_fee
            matches.append({
                "code": code, "buy_date": lot["date"], "sell_date": row["trade_date"],
                "quantity": qty, "buy_price": lot["price"], "sell_price": row["price"],
                "gross_pnl": round(gross, 2), "fees": round(fees, 2),
                "net_pnl": round(gross - fees, 2),
            })
            lot["quantity"] -= qty
            remaining -= qty
            if lot["quantity"] <= 1e-9:
                lots[code].pop(0)
        if remaining > 1e-9:
            unmatched.append({"code": code, "date": row["trade_date"],
                              "quantity": round(remaining, 4), "price": row["price"]})
    open_lots = []
    for code, items in lots.items():
        for lot in items:
            if lot["quantity"] > 1e-9:
                open_lots.append({"code": code, **{key: lot[key] for key in ("date", "quantity", "price")}})
    realized = round(sum(item["net_pnl"] for item in matches), 2)
    return {"matches": matches, "open_lots": open_lots, "unmatched": unmatched,
            "realized": realized, "executions": len(rows)}


def reconcile_with_journal(conn) -> list[dict]:
    """券商未平仓数量 vs 账本未平仓 shares；只提示，不改。"""
    broker_open: dict[str, float] = {}
    for lot in fifo(conn)["open_lots"]:
        broker_open[lot["code"]] = broker_open.get(lot["code"], 0.0) + lot["quantity"]
    journal_open: dict[str, float] = {}
    for row in db.query(conn, "SELECT code, shares FROM trades WHERE closed_on IS NULL"):
        journal_open[row["code"]] = journal_open.get(row["code"], 0.0) + float(row["shares"] or 0)
    out = []
    for code in sorted(set(broker_open) | set(journal_open)):
        left, right = broker_open.get(code, 0.0), journal_open.get(code, 0.0)
        if abs(left - right) > 1e-6:
            out.append({"code": code, "broker": round(left, 4), "journal": round(right, 4)})
    return out


def report(result: dict, journal_diff: list[dict] | None = None) -> str:
    lines = ["===== 券商成交对账 =====",
             f"成交 {result['executions']} 行；已配对 {len(result['matches'])} 笔，"
             f"已实现净盈亏 {result['realized']:+.2f} 元"]
    if result["open_lots"]:
        lines.append("未平仓（FIFO）：")
        for lot in result["open_lots"]:
            lines.append(f"  {lot['code']} {lot['quantity']:g} 股 @ {lot['price']:.3f}（{lot['date']}）")
    if result["unmatched"]:
        lines.append("⚠ 卖出多于买入（缺成交或需手工确认）：")
        for item in result["unmatched"]:
            lines.append(f"  {item['code']} {item['quantity']:g} 股 @ {item['price']:.3f}（{item['date']}）")
    if journal_diff:
        lines.append("⚠ 与交易账本不一致（只提示，不改）：")
        for item in journal_diff:
            lines.append(f"  {item['code']} 券商 {item['broker']:g} vs 账本 {item['journal']:g}")
    else:
        lines.append("与交易账本：数量一致（或账本暂无同标的）。")
    lines.append("")
    lines.append("未处理：分红、送转、拆股、配股。遇到这些先手工核对，不要直接调 FIFO。")
    return "\n".join(lines)
