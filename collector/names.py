"""常见标的的中文名。

数据库里 instruments.name 常常就是代码本身（我们没有代码表落库的习惯），
而页面和分享图上写"沪深300"比写"SH000300"好读，所以这里放一张小对照表。
"""

from __future__ import annotations

KNOWN_NAMES = {
    "SH000001": "上证指数",
    "SH000300": "沪深300",
    "SH000905": "中证500",
    "SH000852": "中证1000",
    "SZ399006": "创业板指",
    "SH510300": "沪深300ETF",
    "SH510050": "上证50ETF",
    "SZ159919": "沪深300ETF(嘉实)",
    "SH510500": "中证500ETF",
    "SH588000": "科创50ETF",
}


def display_name(code: str, fallback: str | None = None) -> str:
    """给代码配一个能读的名字；实在没有就返回空串（调用方自己决定显示什么）。"""
    text = str(code or "").strip().upper()
    name = KNOWN_NAMES.get(text) or (fallback or "").strip()
    return "" if not name or name == text else name
