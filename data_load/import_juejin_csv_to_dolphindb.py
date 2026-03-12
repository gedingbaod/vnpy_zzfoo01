"""
掘金 CSV 数据导入到 DolphinDB
将掘金下载的 Tick 数据 CSV 文件导入到 vnpy 的数据库
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import ast
import pandas as pd
from datetime import datetime
from typing import List, Optional
import logging

from vnpy.trader.object import TickData
from vnpy.trader.constant import Exchange
from vnpy.trader.database import get_database

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class JuejinCsvImporter:
    """掘金 CSV 数据导入器"""

    # 交易所映射：掘金交易所代码 -> vnpy Exchange
    EXCHANGE_MAP: dict[str, Exchange] = {
        "SSE": Exchange.SSE,
        "SZSE": Exchange.SZSE,
        "SHFE": Exchange.SHFE,
        "DCE": Exchange.DCE,
        "CZCE": Exchange.CZCE,
        "CFFEX": Exchange.CFFEX,
        "INE": Exchange.INE,
        "GFEX": Exchange.GFEX,
    }

    def __init__(
        self,
        csv_file: str | Path,
        chunk_size: int = 10000,
    ):
        """
        初始化导入器

        Args:
            csv_file: CSV 文件路径
            chunk_size: 每批次导入的数据量
        """
        self.csv_file = Path(csv_file)
        self.chunk_size = chunk_size

        # 从文件名解析合约信息
        # 文件名格式: 2026-01-21-SHFE.sn2604-tick.csv
        self.symbol, self.exchange = self._parse_symbol_from_filename()

        # 获取数据库连接
        self.database = get_database()
        logger.info(f"数据库类型: {type(self.database).__name__}")

    def _parse_symbol_from_filename(self) -> tuple[str, Exchange]:
        """
        从文件名解析 symbol 和 exchange

        Returns:
            (symbol, exchange) 元组
        """
        # 文件名格式: 2026-01-21-SHFE.sn2604-tick.csv
        filename = self.csv_file.stem  # 去掉 .csv
        parts = filename.split('-')

        if len(parts) >= 4:
            exchange_symbol = parts[3]  # SHFE.sn2604
            if '.' in exchange_symbol:
                exchange_code, symbol = exchange_symbol.split('.')
                exchange = self.EXCHANGE_MAP.get(exchange_code, Exchange.SHFE)
                return symbol, exchange

        raise ValueError(f"无法解析文件名: {self.csv_file.name}")

    def parse_quotes(self, quotes_str: str) -> dict:
        """
        解析 quotes JSON 字符串

        Args:
            quotes_str: quotes 字符串，格式如 "[{'bid_p': 406110.0, 'bid_v': 5, ...}]"

        Returns:
            包含买卖盘数据的字典
        """
        result = {
            "bid_prices": [0.0] * 5,
            "bid_volumes": [0] * 5,
            "ask_prices": [0.0] * 5,
            "ask_volumes": [0] * 5,
        }

        try:
            # 掘金使用的是单引号，需要替换成双引号
            quotes_str = quotes_str.replace("'", '"')
            quotes_list = ast.literal_eval(quotes_str)

            if isinstance(quotes_list, list) and len(quotes_list) > 0:
                quote = quotes_list[0]
                result["bid_prices"][0] = quote.get("bid_p", 0.0)
                result["bid_volumes"][0] = quote.get("bid_v", 0)
                result["ask_prices"][0] = quote.get("ask_p", 0.0)
                result["ask_volumes"][0] = quote.get("ask_v", 0)
        except Exception as e:
            logger.debug(f"解析 quotes 失败: {e}")

        return result

    def csv_row_to_tick(self, row: pd.Series) -> TickData:
        """
        将 CSV 行转换为 TickData 对象

        Args:
            row: pandas Series，一行 CSV 数据

        Returns:
            TickData 对象
        """
        # 解析时间
        created_at = row["created_at"]
        if isinstance(created_at, str):
            # 格式: "2026-01-20 20:59:00.500000+08:00"
            # 去掉时区信息
            if "+" in created_at:
                created_at = created_at.split("+")[0]
            dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S.%f")
        else:
            dt = pd.to_datetime(created_at).to_pydatetime()

        # 解析 quotes
        quotes = self.parse_quotes(row["quotes"])

        # 创建 TickData
        tick = TickData(
            symbol=self.symbol,
            exchange=self.exchange,
            datetime=dt,
            gateway_name="DB",
            name="",
            volume=float(row.get("cum_volume", 0)),
            turnover=float(row.get("cum_amount", 0)),
            open_interest=float(row.get("cum_position", 0)),
            last_price=float(row.get("price", 0)),
            last_volume=float(row.get("last_volume", 0)),
            limit_up=0.0,
            limit_down=0.0,
            open_price=float(row.get("open", 0)),
            high_price=float(row.get("high", 0)),
            low_price=float(row.get("low", 0)),
            pre_close=0.0,
            # 买盘
            bid_price_1=quotes["bid_prices"][0],
            bid_price_2=quotes["bid_prices"][1],
            bid_price_3=quotes["bid_prices"][2],
            bid_price_4=quotes["bid_prices"][3],
            bid_price_5=quotes["bid_prices"][4],
            # 卖盘
            ask_price_1=quotes["ask_prices"][0],
            ask_price_2=quotes["ask_prices"][1],
            ask_price_3=quotes["ask_prices"][2],
            ask_price_4=quotes["ask_prices"][3],
            ask_price_5=quotes["ask_prices"][4],
            # 买量
            bid_volume_1=float(quotes["bid_volumes"][0]),
            bid_volume_2=float(quotes["bid_volumes"][1]),
            bid_volume_3=float(quotes["bid_volumes"][2]),
            bid_volume_4=float(quotes["bid_volumes"][3]),
            bid_volume_5=float(quotes["bid_volumes"][4]),
            # 卖量
            ask_volume_1=float(quotes["ask_volumes"][0]),
            ask_volume_2=float(quotes["ask_volumes"][1]),
            ask_volume_3=float(quotes["ask_volumes"][2]),
            ask_volume_4=float(quotes["ask_volumes"][3]),
            ask_volume_5=float(quotes["ask_volumes"][4]),
            localtime=dt,
        )
        return tick

    def import_to_db(self) -> int:
        """
        导入数据到数据库

        Returns:
            导入的数据条数
        """
        if not self.csv_file.exists():
            logger.error(f"文件不存在: {self.csv_file}")
            return 0

        logger.info(f"开始导入: {self.csv_file.name}")
        logger.info(f"合约: {self.symbol}.{self.exchange.value}")

        # 读取 CSV 文件
        df = pd.read_csv(self.csv_file)
        total_rows = len(df)
        logger.info(f"总记录数: {total_rows}")

        # 分批导入
        imported_count = 0
        ticks_buffer: List[TickData] = []

        for idx, row in df.iterrows():
            try:
                tick = self.csv_row_to_tick(row)
                ticks_buffer.append(tick)

                # 达到批次大小，写入数据库
                if len(ticks_buffer) >= self.chunk_size:
                    self.database.save_tick_data(ticks_buffer)
                    imported_count += len(ticks_buffer)
                    ticks_buffer.clear()
                    logger.info(f"已导入: {imported_count}/{total_rows}")

            except Exception as e:
                logger.error(f"处理第 {idx} 行失败: {e}")
                continue

        # 写入剩余数据
        if ticks_buffer:
            self.database.save_tick_data(ticks_buffer)
            imported_count += len(ticks_buffer)

        logger.info(f"导入完成! 共导入 {imported_count} 条数据")
        return imported_count


def import_single_file(
    csv_file: str | Path,
    chunk_size: int = 10000,
) -> int:
    """
    导入单个 CSV 文件

    Args:
        csv_file: CSV 文件路径
        chunk_size: 每批次导入的数据量

    Returns:
        导入的数据条数
    """
    importer = JuejinCsvImporter(csv_file, chunk_size)
    return importer.import_to_db()


def import_directory(
    directory: str | Path,
    pattern: str = "*-tick.csv",
    chunk_size: int = 10000,
) -> dict[str, int]:
    """
    导入目录下的所有匹配的 CSV 文件

    Args:
        directory: 目录路径
        pattern: 文件匹配模式
        chunk_size: 每批次导入的数据量

    Returns:
        每个文件的导入结果 {filename: count}
    """
    directory = Path(directory)
    csv_files = list(directory.glob(pattern))

    logger.info(f"找到 {len(csv_files)} 个文件")
    results = {}

    for csv_file in csv_files:
        try:
            count = import_single_file(csv_file, chunk_size)
            results[csv_file.name] = count
        except Exception as e:
            logger.error(f"导入 {csv_file.name} 失败: {e}")
            results[csv_file.name] = 0

    return results


def main():
    """主函数 - 示例用法"""

    # 导入单个文件
    csv_file = r"G:\掘金数据\load\juejin_data_2026-01-21\2026-01-21\TickData\2026-01-21-SHFE.sn2604-tick.csv"
    import_single_file(csv_file, chunk_size=10000)

    # 方式2: 导入整个目录
    # import_directory(
    #     r"G:\掘金数据\load\juejin_data_2026-01-21\2026-01-21\TickData",
    #     pattern="*-tick.csv"
    # )


if __name__ == "__main__":
    main()
