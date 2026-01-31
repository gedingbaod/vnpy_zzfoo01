# 忽略各模块的警告信息
import sys
import warnings

import pandas as pd
from gm.api import set_token, get_history_symbol, get_previous_n_trading_dates
from gm.api._utils import _timestamp_to_str
from gm.pb.instrument_service_pb2 import GetTradingDatesPrevNResp, GetTradingDatesPrevNReq
from gm.pb_to_dict import protobuf_to_dict

from common.file_dir import traverse_file_in_dir, load_data_from_csv

warnings.filterwarnings("ignore")

from datetime import datetime

from vnpy.trader.datafeed import get_datafeed
from vnpy.trader.database import get_database
from vnpy.trader.constant import Interval, Exchange
from vnpy.trader.object import TickData, HistoryRequest
from vnpy.trader.setting import SETTINGS

# 交易所映射
EXCHANGE_CTP2VT: dict[str, Exchange] = {
    "CFFEX": Exchange.CFFEX,
    "SHFE": Exchange.SHFE,
    "CZCE": Exchange.CZCE,
    "DCE": Exchange.DCE,
    "INE": Exchange.INE,
    "GFEX": Exchange.GFEX
}

SETTINGS["datafeed.name"] = "gm"
SETTINGS["datafeed.username"] = ""
SETTINGS["datafeed.password"] = "0dc802db1a918f7c064237478e69b83a024028e7"

# 配置数据库
SETTINGS["database.name"] = "dolphindb"
SETTINGS["database.database"] = "vnpy"
SETTINGS["database.host"] = "127.0.0.1"
SETTINGS["database.port"] = 8848
SETTINGS["database.user"] = "admin"
SETTINGS["database.password"] = "123456"

# 创建对象实例
datafeed = get_datafeed()
database = get_database()

log_file =open("log.txt", "a")
def log_write(msg):
    out_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out_str += "   "
    out_str += str(msg)
    out_str += "\n"
    log_file.write(out_str)
    print(out_str)
    log_file.flush()

def get_dates_from_range(start_date: str, end_date: str)-> list:

    date_df = get_history_symbol(symbol='CFFEX.IC', start_date=start_date, end_date=end_date, df= True)

    if date_df.empty:
        return []

    if pd.api.types.is_datetime64_any_dtype(date_df['trade_date']):
        date_list = date_df['trade_date'].dt.strftime('%Y-%m-%d').tolist()
    else:
        # 如果是字符串类型，先转换为 datetime 再格式化
        date_list = pd.to_datetime(date_df['trade_date']).dt.strftime('%Y-%m-%d').tolist()

    return date_list

def set_specific_time(dt_obj, hour=0, minute=0, second=0):
    """
    修改datetime对象的时间部分
    参数:
        dt_obj (datetime): 原始datetime对象
        hour (int): 目标小时(0-23)
        minute (int): 目标分钟(0-59)
        second (int): 目标秒(0-59)
    返回:
        datetime: 新时间点的datetime对象
    """
    return dt_obj.replace(hour=hour, minute=minute, second=second, tzinfo=None)

def get_trading_datetime(base_date: datetime)-> (datetime, datetime):
    """
    计算给定日期的下一日
    参数:
        date_str (str): 格式为'YYYY-MM-DD'的日期字符串
    返回:
        str: 下一日的日期字符串，格式为'YYYY-MM-DD'
    """
    # 获取前一个交易日
    start_time_list = get_previous_n_trading_dates(exchange='CFFEX', date=base_date.strftime('%Y-%m-%d'))
    # start_time = base_date - timedelta(days=1)
    # 交易开始时间   交易日开始的前一天晚上
    start_time = set_specific_time(datetime.strptime(start_time_list[0], "%Y-%m-%d"), 20, 0, 0)
    # 交易结束时间   这一天是交易日
    end_time = set_specific_time(base_date, 16, 0, 0)
    return start_time, end_time

def main(csv_path):
    result: bool = datafeed.init()
    if result:
        print("数据服务初始化成功")

    # 获取所有合约
    file_list = traverse_file_in_dir(csv_path)

    if file_list is None:
        log_write("没有找到CSV文件")
        return

    # list_loaded = database.get_bar_overview()

    # 遍历文件列表，一个文件一个商品
    for file_path in file_list:
        # if file_path.name.endswith('test.csv') is False:
        #     continue
        log_write(f'导入文件：{file_path}')
        instruments_df = load_data_from_csv(file_path)
        # 商品合约总数
        total = len(instruments_df)
        # 遍历该商品全部合约
        for idx, row in instruments_df.iterrows():
            # 提取当前行的字段值
            exchange = EXCHANGE_CTP2VT[row['exchange']]
            sec_id = row['sec_id']
            # Timestamp转为datetime，测试过没有损失精度
            listed_date = row['listed_date'].to_pydatetime()
            delisted_date = row['delisted_date'].to_pydatetime()
            listed_date = listed_date.replace(tzinfo=None)
            delisted_date = delisted_date.replace(tzinfo=None)

            local_time = datetime(2026, 1, 30)


            if delisted_date > local_time:
                local_time_str = local_time.strftime("%Y-%m-%d")
                date_list = get_dates_from_range(local_time_str, local_time_str)
                # if len(date_list) == 0:
                #     sys.exit(1)
                start_time, end_time = get_trading_datetime(local_time)

                # 打印行数据（可替换为你的业务逻辑）
                # log_write(f"索引：{idx+1} 总数：{total} | 交易所：{exchange} "
                #           f"| 合约代码：{sec_id} | 上市日期：{listed_date} | 退市日期：{delisted_date}")

                vnpy_tick_process(sec_id, exchange, start_time, end_time, Interval.TICK)

                # if not check_loaded(symbol=sec_id, exchange=exchange, list_loaded=list_loaded):
                #     log_write(f"{sec_id}.{exchange} 未下载")

# def check_loaded(symbol, exchange, list_loaded):
#     for row in list_loaded:
#         if row.symbol == symbol and row.exchange.value == exchange.value:
#             return True

def vnpy_tick_process(symbol, exchange, start, end, interval):
    # 创建历史数据请求对象
    req: HistoryRequest = HistoryRequest(
        symbol=symbol,
        exchange=exchange,
        start=start,
        end=end,
        interval=interval  # Interval.MINUTE,Interval.HOUR,Interval.DAILY,Interval.WEEKLY
    )
    try:
        # 从数据服务下载数据
        ticks: list[TickData] = datafeed.query_tick_history(req)
        log_write(f"下载数据成功：{symbol}.{exchange}，周期：{interval}，总数据量：{len(ticks)}")
        # 如果下载成功则保存
        if ticks:
            database.save_tick_data(ticks)
            log_write(f"保存数据成功：{symbol}.{exchange}，周期：{interval}，总数据量：{len(ticks)}")
        # 否则失败则打印信息
        else:
            log_write(f"数据为空：{symbol}.{exchange}")
    except Exception as e:
        log_write(f"========下载数据异常：{symbol}.{exchange}")
        log_write(e)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # main(r'D:\02.QUANT\QuantData\DataFeedEx\juejin\data\test')
    main(r'I:\QUANT\QuantData\DataFeedEx\juejin\data\contracts_csv_202601')
    pass


