"""SQLite 小工具：不用装任何东西，用 Python 自带的 sqlite3 就能查库、看表。

用法：
    python sql.py tables                 列出所有表和行数
    python sql.py schema alerts          看某张表的建表语句
    python sql.py "SELECT * FROM alerts LIMIT 10"     执行查询，打印表格
    python sql.py browser                生成可视化浏览页 db_view.html 并打开

默认数据库：data/market.db（可用 --db 指定）
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = PROJECT_ROOT / "data" / "market.db"


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}\n先跑一次 python run.py daily。")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _width(text: str) -> int:
    """中文按两个字符宽度计算，保证表格对齐。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(text))


def _pad(text: str, target: int) -> str:
    return str(text) + " " * max(0, target - _width(text))


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def cmd_tables(conn) -> None:
    names = list_tables(conn)
    if not names:
        print("（空库，还没有任何表）")
        return
    print(f"{'表名':<24}{'行数':>10}")
    print("-" * 36)
    for name in names:
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
        print(f"{_pad(name, 24)}{count:>10}")


def cmd_schema(conn, table: str) -> None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None:
        raise SystemExit(f"没有这张表：{table}")
    print(row["sql"])


def cmd_query(conn, sql: str, limit_width: int = 34) -> None:
    try:
        cursor = conn.execute(sql)
    except sqlite3.Error as exc:
        raise SystemExit(f"SQL 出错：{exc}")
    rows = cursor.fetchall()
    if not rows:
        print("（没有结果）")
        return

    columns = rows[0].keys()
    cells = []
    for row in rows:
        cells.append(
            [
                (str(row[col]) if row[col] is not None else "NULL")[:limit_width]
                for col in columns
            ]
        )
    widths = [
        min(limit_width, max(_width(col) for col in [columns[i]] + [line[i] for line in cells]))
        for i in range(len(columns))
    ]

    print("  ".join(_pad(col, widths[i]) for i, col in enumerate(columns)))
    print("  ".join("-" * widths[i] for i in range(len(columns))))
    for line in cells:
        print("  ".join(_pad(value, widths[i]) for i, value in enumerate(line)))
    print(f"\n共 {len(rows)} 行")


def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def cmd_browser(conn, out_path: Path, db_path: Path, rows_per_table: int = 200) -> None:
    names = list_tables(conn)
    sections = []
    nav = []

    for name in names:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
        rows = conn.execute(f"SELECT * FROM {name} LIMIT {rows_per_table}").fetchall()
        columns = rows[0].keys() if rows else []
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()["sql"]
        nav.append(f'<button type="button" class="tab" data-target="t-{name}">{name} <span>{total}</span></button>')

        head = "".join(f"<th>{_html_escape(col)}</th>" for col in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{_html_escape(row[col]) if row[col] is not None else '<i>NULL</i>'}</td>" for col in columns) + "</tr>"
            for row in rows
        )
        note = (
            f"共 {total} 行，此处显示前 {min(total, rows_per_table)} 行"
            if total > rows_per_table
            else f"共 {total} 行"
        )
        sections.append(
            f'<section id="t-{name}" class="panel" hidden>'
            f"<h2>{name}</h2><p class='meta'>{note}</p>"
            f"<details><summary>建表语句</summary><pre>{_html_escape(ddl)}</pre></details>"
            f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
            f"</section>"
        )

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>数据库浏览 · {_html_escape(db_path.name)}</title>
<style>
  :root {{ --bg:#f6f7f9; --panel:#fff; --fg:#1b2430; --muted:#6b7684; --line:#e3e7ed; --accent:#2f6fd0; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#14181d; --panel:#1c2229; --fg:#e8ecf1; --muted:#9aa5b1; --line:#2c343d; --accent:#6fa2e8; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:22px; background:var(--bg); color:var(--fg);
    font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif; line-height:1.6; }}
  .wrap {{ max-width:1180px; margin:0 auto; }}
  h1 {{ font-size:19px; margin:0 0 4px; }}
  h2 {{ font-size:15px; margin:0 0 4px; }}
  .meta {{ color:var(--muted); font-size:12.5px; margin:0 0 10px; }}
  .tabs {{ display:flex; flex-wrap:wrap; gap:6px; margin:14px 0; }}
  .tab {{ font:inherit; font-size:12.5px; padding:4px 10px; border:1px solid var(--line); border-radius:20px;
    background:var(--panel); color:var(--fg); cursor:pointer; }}
  .tab span {{ color:var(--muted); }}
  .tab.active {{ border-color:var(--accent); color:var(--accent); }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .scroll {{ overflow:auto; max-height:70vh; margin-top:10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th,td {{ text-align:left; padding:5px 9px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th {{ position:sticky; top:0; background:var(--panel); color:var(--muted); font-weight:500; }}
  td i {{ color:var(--muted); }}
  pre {{ background:var(--bg); border:1px solid var(--line); border-radius:7px; padding:9px; overflow:auto; font-size:12px; }}
  details summary {{ cursor:pointer; color:var(--muted); font-size:12.5px; }}
</style></head>
<body><div class="wrap">
  <h1>数据库浏览</h1>
  <p class="meta">{_html_escape(db_path)} · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 共 {len(names)} 张表（只读快照）</p>
  <div class="tabs">{"".join(nav)}</div>
  {"".join(sections)}
</div>
<script>
  var tabs = document.querySelectorAll(".tab");
  var panels = document.querySelectorAll("section.panel");
  function show(id) {{
    panels.forEach(function (p) {{ p.hidden = p.id !== id; }});
    tabs.forEach(function (t) {{ t.classList.toggle("active", t.dataset.target === id); }});
  }}
  tabs.forEach(function (t) {{ t.addEventListener("click", function () {{ show(t.dataset.target); }}); }});
  if (tabs.length) {{ show(tabs[0].dataset.target); }}
</script>
</body></html>
"""
    out_path.write_text(html, encoding="utf-8")
    print(f"浏览页已生成：{out_path}")


def main(argv: list[str]) -> int:
    args = list(argv)
    db_path = DEFAULT_DB
    if "--db" in args:
        index = args.index("--db")
        db_path = Path(args[index + 1]).resolve()
        del args[index:index + 2]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    conn = connect(db_path)
    command = args[0]

    if command == "tables":
        cmd_tables(conn)
    elif command == "schema":
        if len(args) < 2:
            raise SystemExit("用法：python sql.py schema <表名>")
        cmd_schema(conn, args[1])
    elif command == "browser":
        out = PROJECT_ROOT / "db_view.html"
        if "--out" in args:
            out = Path(args[args.index("--out") + 1]).resolve()
        cmd_browser(conn, out, db_path)
    else:
        cmd_query(conn, " ".join(args))

    conn.close()
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main(sys.argv[1:]))
