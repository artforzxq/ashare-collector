"""adata 适配器（免费，多源自动切换，建议作为主源）。

adata 的函数名在不同版本间有调整，这里不绑死单一函数名，而是按候选路径探测，
探测失败时给出明确提示，避免默默返回空数据。启用前请对照当期文档确认路径。
"""

from __future__ import annotations

from .base import BaseSource, DataSourceError, canonicalize_frame
from . import split_code


def _resolve(node, dotted: str):
    for part in dotted.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    return node


class AdataSource(BaseSource):
    name = "adata"
    capabilities = {"daily_bars"}

    # (函数路径, 传入参数) —— 按顺序尝试，第一个返回非空结果的生效
    DAILY_CANDIDATES = (
        ("stock.market.get_market", ("stock_code", "start_date", "end_date")),
        ("stock.market.get_market_hist", ("stock_code", "start_date", "end_date")),
    )

    def _adata(self):
        try:
            import adata
        except ImportError as exc:  # pragma: no cover - 依赖缺失分支
            raise DataSourceError("未安装 adata：pip install adata") from exc
        return adata

    def is_available(self) -> tuple[bool, str]:
        try:
            self._adata()
        except DataSourceError as exc:
            return False, str(exc)
        return True, "已安装（函数路径需按当期文档核对）"

    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        adata = self._adata()
        _, symbol = split_code(code)
        errors: list[str] = []

        for path, params in self.DAILY_CANDIDATES:
            func = _resolve(adata, path)
            if func is None:
                errors.append(f"{path} 不存在")
                continue
            kwargs = {}
            for name in params:
                if name == "stock_code":
                    kwargs[name] = symbol
                elif name == "start_date":
                    kwargs[name] = start
                elif name == "end_date":
                    kwargs[name] = end
            try:
                frame = func(**kwargs)
            except TypeError as exc:
                errors.append(f"{path} 参数不匹配：{exc}")
                continue
            except Exception as exc:  # pragma: no cover - 网络/接口异常
                errors.append(f"{path} 调用失败：{exc}")
                continue

            rows = canonicalize_frame(frame)
            rows = [r for r in rows if start <= (r.get("trade_date") or "") <= end]
            if rows:
                for row in rows:
                    row["code"] = code
                    row["source"] = self.name
                return rows
            errors.append(f"{path} 返回空结果")

        raise DataSourceError(
            "adata 未能返回数据，请对照当期文档核对接口路径后修改 DAILY_CANDIDATES。"
            f" 尝试记录：{'; '.join(errors)}"
        )
