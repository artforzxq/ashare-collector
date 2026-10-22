"""行业分类：板块研究的地基。

两条入口，都是免费、免账号：
  1. `python run.py industries --sync`：baostock 的证监会行业分类，一次覆盖全市场；
  2. `python run.py industries --import data/industry.csv`：断网时手工/外部映射导入。

口径纪律（和笔记里的"尺子中途不换"同一条）：
  - 选定一个分类体系后写进 `industry_classification`，不混用申万与证监会；
  - 概念板块会重复计算，所以**成交占比用行业口径，叙事才用概念**；
  - 行业标签是快照，不是历史。回测过去某天时，用的是**今天的行业**——
    这对"行业轮动"是近似，不适用于成分股频繁变更的精细结论，报告里要写明。
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import db
from .sources import build_source


class IndustryError(RuntimeError):
    """给人看的错误：没有源、CSV 格式不对、覆盖不足。"""


ALIASES = {
    "code": "code", "代码": "code", "证券代码": "code", "股票代码": "code",
    "name": "name", "名称": "name", "证券简称": "name", "股票名称": "name",
    "industry": "industry", "行业": "industry", "所属行业": "industry",
    "industry_classification": "industry_classification", "分类体系": "industry_classification",
}


def normalize_code(value: str) -> str | None:
    from .sources.base import exchange_of, normalize_symbol

    text = str(value or "").strip()
    if not text:
        return None
    symbol = normalize_symbol(text)
    if len(symbol) != 6 or not symbol.isdigit():
        return None
    return exchange_of(text) + symbol


def apply_rows(conn, rows: list[dict], source: str,
               snapshot_date: str | None = None) -> dict:
    """把行业映射写进 instruments。只更新代码表里已有的标的，缺的补一条最小记录。"""
    existing = {
        row["code"]: row["type"]
        for row in db.query(conn, "SELECT code, type FROM instruments")
    }
    records = []
    for row in rows:
        code = normalize_code(row.get("code", ""))
        industry = (row.get("industry") or "").strip()
        if not code or not industry:
            continue
        record = {
            "code": code,
            "name": (row.get("name") or "").strip() or None,
            "exchange": code[:2],
            "industry": industry,
            "industry_classification": row.get("industry_classification") or source,
            "industry_source": source,
            "industry_updated": row.get("industry_updated") or db.now_iso()[:10],
        }
        # 已有标的不要用行业导入覆盖它的类型（ETF/指数被改成 stock 会让覆盖率与筛选全乱）。
        # 只有代码表里没有的新代码，才按个股补一条最小记录。
        if code not in existing:
            record["type"] = row.get("type") or "stock"
        records.append(record)
    if not records:
        raise IndustryError("没有可写入的行业记录（检查 code / industry 两列）")
    written = db.upsert_rows(conn, "instruments", records, ["code"])
    snapshot = snapshot_date or db.now_iso()[:10]
    history = [{
        "snapshot_date": snapshot,
        "code": row["code"],
        "industry": row["industry"],
        "industry_classification": row.get("industry_classification"),
        "source": row.get("industry_source"),
        "created_at": db.now_iso(),
    } for row in records]
    history_written = db.upsert_rows(
        conn, "industry_history", history, ["snapshot_date", "code"])
    return {"ok": True, "written": written, "history_written": history_written,
            "source": source}


def sync(conn, cfg: dict, verbose: bool = False) -> dict:
    """从 baostock 拉全市场行业分类。"""
    try:
        source = build_source("baostock", cfg)
    except Exception as exc:
        raise IndustryError(f"baostock 不可用：{exc}") from exc
    if not hasattr(source, "industry_directory"):
        raise IndustryError("当前 baostock 适配器没有行业分类能力")
    try:
        rows = source.industry_directory()
    except Exception as exc:
        raise IndustryError(f"行业分类拉取失败：{exc}") from exc
    result = apply_rows(conn, rows, "baostock")
    if verbose:
        print(f"  行业分类：写入 {result['written']} 只（baostock / 证监会口径）")
    return result


def _read_csv(path: str | Path) -> list[dict]:
    target = Path(path).expanduser()
    if not target.exists():
        raise IndustryError(f"找不到文件：{target}")
    text = target.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise IndustryError("CSV 是空的")
    first = next(csv.reader([lines[0]]))
    has_header = any((cell or "").strip().lower() in ALIASES for cell in first)
    records = []
    if has_header:
        reader = csv.DictReader(lines)
        for raw in reader:
            row = {}
            for key, value in raw.items():
                mapped = ALIASES.get((key or "").strip().lower()) or ALIASES.get((key or "").strip())
                if mapped:
                    row[mapped] = value
            records.append(row)
    else:
        for line in lines:
            cells = next(csv.reader([line]))
            if len(cells) < 2:
                continue
            records.append({"code": cells[0], "industry": cells[1],
                            "name": cells[2] if len(cells) > 2 else None})
    return records


def import_csv(conn, path: str | Path, classification: str = "manual") -> dict:
    rows = _read_csv(path)
    for row in rows:
        row["industry_classification"] = row.get("industry_classification") or classification
    result = apply_rows(conn, rows, f"csv:{Path(path).name}")
    result["rows"] = len(rows)
    return result


def coverage(conn) -> dict:
    """行业标签覆盖率。低于 80% 时不做板块结论——半张地图比没有地图更危险。"""
    row = db.query_one(
        conn,
        """SELECT
             SUM(CASE WHEN type='stock' OR type IS NULL THEN 1 ELSE 0 END) AS stocks,
             SUM(CASE WHEN (type='stock' OR type IS NULL) AND industry IS NOT NULL
                           AND industry <> '' THEN 1 ELSE 0 END) AS tagged,
             COUNT(DISTINCT CASE WHEN (type='stock' OR type IS NULL)
                                      AND industry IS NOT NULL AND industry <> ''
                                 THEN industry END) AS industries,
             MIN(industry_updated) AS oldest,
             MAX(industry_updated) AS newest
           FROM instruments""",
    )
    stocks = int(row["stocks"] or 0)
    tagged = int(row["tagged"] or 0)
    return {
        "stocks": stocks,
        "tagged": tagged,
        "coverage": round(tagged / stocks * 100, 1) if stocks else 0.0,
        "industries": int(row["industries"] or 0),
        "oldest": row["oldest"],
        "newest": row["newest"],
    }


def mapping(conn) -> dict[str, str]:
    return {
        row["code"]: row["industry"]
        for row in db.query(
            conn,
            "SELECT code, industry FROM instruments WHERE industry IS NOT NULL AND industry <> ''",
        )
    }


def mapping_asof(conn, day: str) -> tuple[dict[str, str], str | None]:
    """某日可用的行业映射。返回 (映射, 实际快照日)。

    有 <= day 的历史快照就用最近的那份；没有就退回当前映射，快照日返回 None，
    调用方必须把"这是近似口径"说清楚。
    """
    row = db.query_one(
        conn,
        "SELECT MAX(snapshot_date) AS d FROM industry_history WHERE snapshot_date<=?",
        (day,),
    )
    snapshot = row["d"] if row else None
    if not snapshot:
        return mapping(conn), None
    return {
        item["code"]: item["industry"]
        for item in db.query(
            conn,
            "SELECT code, industry FROM industry_history WHERE snapshot_date=?",
            (snapshot,),
        )
    }, snapshot


def history_coverage(conn) -> dict:
    row = db.query_one(
        conn,
        """SELECT COUNT(*) AS rows, COUNT(DISTINCT snapshot_date) AS snapshots,
                  MIN(snapshot_date) AS oldest, MAX(snapshot_date) AS newest
             FROM industry_history""",
    )
    return {
        "rows": int(row["rows"] or 0),
        "snapshots": int(row["snapshots"] or 0),
        "oldest": row["oldest"],
        "newest": row["newest"],
    }


def report(conn) -> str:
    cov = coverage(conn)
    lines = ["===== 行业分类 =====",
             f"个股 {cov['stocks']:,} 只，已标注 {cov['tagged']:,} 只"
             f"（覆盖 {cov['coverage']:.1f}%），{cov['industries']} 个行业"]
    if cov["newest"]:
        lines.append(f"标签日期：{cov['oldest']} → {cov['newest']}")
    if cov["coverage"] < 80:
        lines.append("⚠ 覆盖不足 80%，暂不做板块结论。")
        lines.append("  python run.py industries --sync   # 免费源，一步覆盖全市场")
        lines.append("  python run.py industries --import data/industry.csv")
    else:
        lines.append("✓ 覆盖够用。注意：行业标签是当前快照，回测过去是近似口径。")
    hist = history_coverage(conn)
    if hist["snapshots"]:
        lines.append(f"历史快照：{hist['snapshots']} 个日期（{hist['oldest']} → {hist['newest']}）；"
                     "从第一个快照日起，回看是精确的，之前仍是近似。")
    else:
        lines.append("历史快照：还没有。下一次 industries --sync 会自动留一份；"
                     "过去无法用免费源补齐。")
    return "\n".join(lines)
