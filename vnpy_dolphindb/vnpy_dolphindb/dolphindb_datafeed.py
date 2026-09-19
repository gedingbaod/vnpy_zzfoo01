from datetime import timedelta, datetime
from collections.abc import Callable
import traceback


import numpy as np
import pandas as pd
import dolphindb as ddb
import gc

from pandas import DataFrame

from vnpy.trader.datafeed import BaseDatafeed
from vnpy.trader.setting import SETTINGS
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, TickData, HistoryRequest
from vnpy.trader.utility import ZoneInfo

from vnpy.trader.database import (
    BaseDatabase,
    BarOverview,
    TickOverview,
    DB_TZ,
    convert_tz
)


INTERVAL_VT2TQ: dict[Interval, int] = {
    Interval.MINUTE: 60,
    Interval.HOUR: 60 * 60,
    Interval.DAILY: 60 * 60 * 24
}

CHINA_TZ = ZoneInfo("Asia/Shanghai")


class DolphindbDatafeed(BaseDatafeed):
    """天勤TQsdk数据服务接口"""

    def __init__(self) -> None:
        """"""
        # self.username: str = SETTINGS["datafeed.username"]
        # self.password: str = SETTINGS["datafeed.password"]

        """构造函数"""
        self.user: str = SETTINGS["database.user"]
        self.password: str = SETTINGS["database.password"]
        self.host: str = SETTINGS["database.host"]
        self.port: int = SETTINGS["database.port"]
        self.db_path: str = "dfs://" + SETTINGS["database.database"]

        # 连接数据库
        self.session: ddb.session = ddb.session()
        self.session.connect(self.host, self.port, self.user, self.password)

        # 创建连接池（用于数据写入）
        self.pool: ddb.DBConnectionPool = ddb.DBConnectionPool(self.host, self.port, 1, self.user, self.password)


    def query_bar_history(self, req: HistoryRequest, output: Callable = print) -> list[BarData] | None:
        """查询k线数据"""


        # 查询数据
        interval: int | None = INTERVAL_VT2TQ.get(req.interval, None)
        if not interval:
            output(f"DolphinDB查询K线数据失败：不支持的时间周期{req.interval.value}")
            return []

        bars: list[BarData] = []
        bars = self.load_bar_data(symbol=req.symbol, exchange=req.exchange, interval=req.interval, start=req.start, end=req.end)
        return bars


    def load_bar_data(
        self,
        symbol: str,
        exchange: Exchange,
        interval: Interval,
        start: datetime,
        end: datetime
    ) -> list[BarData]:
        """读取K线数据"""
        # 转换时间格式
        _start: np.datetime64 = np.datetime64(start)
        start_str: str = str(_start).replace("-", ".")

        _end: np.datetime64 = np.datetime64(end)
        end_str: str = str(_end).replace("-", ".")

        table: ddb.Table = self.session.loadTable(tableName="bar", dbPath=self.db_path)

        df: pd.DataFrame = (
            table.select('*')
            .where(f'symbol="{symbol}"')
            .where(f'exchange="{exchange.value}"')
            .where(f'interval="{interval.value}"')
            .where(f'datetime>={start_str}')
            .where(f'datetime<={end_str}')
            .toDF()
        )

        if df.empty:
            return []

        df.set_index("datetime", inplace=True)
        df = df.tz_localize(DB_TZ.key)

        # 转换为BarData格式
        bars: list[BarData] = []

        for tp in df.itertuples():
            bar = BarData(
                symbol=symbol,
                exchange=exchange,
                datetime=tp.Index.to_pydatetime(),
                interval=interval,
                volume=tp.volume,
                turnover=tp.turnover,
                open_interest=tp.open_interest,
                open_price=tp.open_price,
                high_price=tp.high_price,
                low_price=tp.low_price,
                close_price=tp.close_price,
                gateway_name="DB"
            )
            bars.append(bar)

        return bars