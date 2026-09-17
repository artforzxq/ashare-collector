"""数据源适配器注册表。"""

from __future__ import annotations

from .base import BaseSource, DataSourceError

SOURCE_REGISTRY: dict[str, str] = {
    "fixture": "collector.sources.fixture:FixtureSource",
    "fixture_alt": "collector.sources.fixture:FixtureAltSource",
    "csv": "collector.sources.fixture:CsvSource",
    "akshare": "collector.sources.akshare_source:AkshareSource",
    "tencent": "collector.sources.tencent_source:TencentSource",
    "sina": "collector.sources.sina_source:SinaSource",
    "adata": "collector.sources.adata_source:AdataSource",
    "baostock": "collector.sources.baostock_source:BaostockSource",
}


def build_source(name: str, cfg: dict) -> BaseSource:
    """按名字实例化数据源。未安装依赖时抛出携带安装提示的 DataSourceError。"""
    import importlib

    target = SOURCE_REGISTRY.get(name)
    if not target:
        raise DataSourceError(f"未知数据源：{name}，可选 {sorted(SOURCE_REGISTRY)}")
    module_name, class_name = target.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)(cfg)


def split_code(code: str) -> tuple[str, str]:
    """SH510300 -> ('SH', '510300')。"""
    text = code.strip().upper()
    if "." in text:  # sh.510300
        exchange, symbol = text.split(".", 1)
        return exchange.upper(), symbol
    return text[:2], text[2:]


__all__ = ["BaseSource", "DataSourceError", "SOURCE_REGISTRY", "build_source", "split_code"]
