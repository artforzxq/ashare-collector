"""baostock 适配器（免费、稳定、更新慢，适合当兜底源）。

注意：baostock 对 ETF 覆盖有限，ETF 建议走 akshare；启用前用 `cli sources` 自检。
"""

from __future__ import annotations

import atexit
import socket
from datetime import datetime, timedelta

from .base import BaseSource, DataSourceError, classify_symbol, exchange_of, normalize_symbol
from . import split_code

FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg"

# baostock 的 socketutil 建连和收数据都没有设超时：
#   登录时的 connect() 最长会跟着系统 TCP 超时卡 75 秒，
#   收数据时的 recv() 在服务端不回应时会**永久阻塞**——定时任务会就这么挂住。
# 这里给它兜一个默认超时，让失败变成异常，而不是无限等待。
SOCKET_TIMEOUT = 30


class BaostockSource(BaseSource):
    name = "baostock"
    capabilities = {"daily_bars", "trade_calendar", "symbol_directory", "stock_basic"}

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self._logged_in = False
        self._login_failed = False

    def _bs(self):
        try:
            import baostock as bs
        except ImportError as exc:  # pragma: no cover - 依赖缺失分支
            raise DataSourceError("未安装 baostock：pip install baostock") from exc
        return bs

    def _login(self):
        """同一进程只登录一次。

        日终要连拉 10 个标的，原来每只都登录、登出各一次，光握手就浪费几十秒。
        这里改成实例级复用，进程结束时统一登出；盘中长循环也只需要登录一次。
        """
        bs = self._bs()
        if self._login_failed:
            # 熔断：本次运行登录已经失败过，剩余标的直接跳过，
            # 免得 10 个标的各等一次超时（30 秒 × 10 = 5 分钟）。
            raise DataSourceError("baostock 本次运行登录失败过，后续标的已跳过")
        if not self._logged_in:
            socket.setdefaulttimeout(SOCKET_TIMEOUT)   # 必须先设，socket 在登录时才创建
            try:
                login = bs.login()
            except Exception as exc:
                self._login_failed = True
                self._close_socket()
                raise DataSourceError(f"baostock 登录异常：{exc}") from exc
            if getattr(login, "error_code", "0") != "0":
                self._login_failed = True
                self._close_socket()      # 登录失败也要把 socket 关掉，否则退出时刷 ResourceWarning
                raise DataSourceError(f"baostock 登录失败：{login.error_msg}")
            self._logged_in = True
            atexit.register(self.logout)
        return bs

    def logout(self) -> None:
        if not self._logged_in:
            return
        self._logged_in = False
        try:
            self._bs().logout()
        except Exception:  # 进程退出阶段不抛异常
            pass

    def _close_socket(self) -> None:
        """baostock 登录失败时会在模块全局留一个没关的 socket，这里替它收尾。"""
        try:
            from baostock.common import context

            sock = getattr(context, "default_socket", None)
            if sock is not None:
                sock.close()
                context.default_socket = None
        except Exception:
            pass

    def is_available(self) -> tuple[bool, str]:
        try:
            self._bs()
            return True, "已安装"
        except DataSourceError as exc:
            return False, str(exc)

    def symbol_directory(self) -> list[dict]:
        """全市场证券清单：个股 + ETF + 指数。

        baostock 的 query_all_stock 按交易日返回当天**所有证券**（实测 7401 行，
        含指数、ETF、场内货币基金；债券和 B 股不在里面），落库前按代码段分类。
        它不覆盖北交所，那部分靠新浪补。
        """
        bs = self._login()
        day = self._directory_day()
        try:
            result = bs.query_all_stock(day=day)
            records: list[dict] = []
            while result.error_code == "0" and result.next():
                records.append(dict(zip(result.fields, result.get_row_data())))
            if result.error_code != "0":
                raise DataSourceError(f"baostock 代码表查询失败：{result.error_msg}")
        except Exception:
            self.logout()
            raise
        return _directory_rows(records)

    def _directory_day(self) -> str:
        """query_all_stock 只认交易日：传周末/节假日它会返回空表。"""
        today = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        try:
            days = [row["trade_date"] for row in self.trade_calendar(start, today) if row["is_trading_day"]]
        except Exception:
            return today
        return days[-1] if days else today

    @staticmethod
    def _bs_code(code: str) -> str:
        exchange, symbol = split_code(code)
        return f"{exchange.lower()}.{symbol}"

    def stock_basic(self, code: str) -> dict:
        """单只标的的基础信息：上市日（ipoDate）、退市日、类型、状态。

        批量代码表接口不给上市日，只能一只一只问——所以调用方要分批、可续跑。
        """
        bs = self._login()
        try:
            result = bs.query_stock_basic(code=self._bs_code(code))
            rows: list[dict] = []
            while result.error_code == "0" and result.next():
                rows.append(dict(zip(result.fields, result.get_row_data())))
            if result.error_code != "0":
                raise DataSourceError(f"baostock 基础信息查询失败：{result.error_msg}")
        except Exception:
            self.logout()
            raise
        return rows[0] if rows else {}

    def trade_calendar(self, start: str, end: str) -> list[dict]:
        """交易所日历，含休市日（is_trading_day=0）。"""
        bs = self._login()
        rows: list[dict] = []
        try:
            result = bs.query_trade_dates(start_date=start, end_date=end)
            while result.error_code == "0" and result.next():
                date_text, is_open = result.get_row_data()[:2]
                rows.append({"trade_date": date_text, "is_trading_day": 1 if is_open == "1" else 0})
            if result.error_code != "0":
                raise DataSourceError(f"baostock 交易日历查询失败：{result.error_msg}")
        except Exception:
            self.logout()
            raise
        return rows

    def daily_bars(self, code: str, start: str, end: str, kind: str = "stock") -> list[dict]:
        bs = self._login()
        try:
            result = bs.query_history_k_data_plus(
                self._bs_code(code),
                FIELDS,
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="2",  # 2=前复权：特征层一律用 close_adj，不复权会在除权日制造假跳空
            )
            rows: list[dict] = []
            while result.error_code == "0" and result.next():
                record = dict(zip(result.fields, result.get_row_data()))
                rows.append(
                    {
                        "code": code,
                        "trade_date": record["date"],
                        "open": _to_float(record.get("open")),
                        "high": _to_float(record.get("high")),
                        "low": _to_float(record.get("low")),
                        "close": _to_float(record.get("close")),
                        "pre_close": None,
                        "volume": _to_float(record.get("volume")),
                        "amount": _to_float(record.get("amount")),
                        "turnover_rate": _to_float(record.get("turn")),
                        "pct_chg": _to_float(record.get("pctChg")),
                        "adj_factor": 1.0,
                        "close_adj": _to_float(record.get("close")),
                        "source": self.name,
                    }
                )
        except Exception:
            self.logout()          # 会话可能已经坏了，让下次调用重新登录
            raise

        rows.sort(key=lambda r: r["trade_date"])
        for index, row in enumerate(rows):
            if row["pre_close"] is None and index:
                row["pre_close"] = rows[index - 1]["close"]
        return rows


def _directory_rows(records) -> list[dict]:
    """baostock 的行 → 全市场清单（个股 / ETF / 指数，其它丢掉）。"""
    rows: list[dict] = []
    for record in records:
        raw = record.get("code")
        symbol = normalize_symbol(raw)
        kind = classify_symbol(raw)
        if kind == "other":
            continue
        rows.append(
            {
                "code": exchange_of(raw) + symbol,
                "name": str(record.get("code_name") or "").strip(),
                "type": kind,
            }
        )
    return rows


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
