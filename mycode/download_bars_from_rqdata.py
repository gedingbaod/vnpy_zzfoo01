# 忽略各模块的警告信息
import warnings

from rqdatac.services.basic import instruments

from common.file_dir import traverse_file_in_dir, load_data_from_csv

warnings.filterwarnings("ignore")

from datetime import datetime

from vnpy.trader.datafeed import get_datafeed
from vnpy.trader.database import get_database, DB_TZ
from vnpy.trader.constant import Interval, Exchange
from vnpy.trader.object import BarData, HistoryRequest
from vnpy.trader.utility import extract_vt_symbol
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

# 配置数据服务
SETTINGS["datafeed.name"] = "rqdata"            # 可以根据自己的需求选择数据服务：rqdata/xt/wind等
SETTINGS["datafeed.username"] = "license"       # RQData的用户名统一为“license”这个字符串
SETTINGS["datafeed.password"] = "dsSCpoxgtMiI4lkILRFJncLB3eMttaiqIhDstum0fTRrVCG5tjoDfv_2-LikVPXvG5jMkduQlRGUASm1_2Bs3hE9tmGCGlUObADeEY5GyeAP3y-Ggrd0ebO7L8e-s3J16EFZW7xRJyu-rNgysG8dVHRBOrdL1ZHUDkLJpsKhZlU=XZh2iFOUVV1hk-JhpZ4seKUSGDMTzUQBOsOPP_Gxf3H-zpa1KjQPiQrw01d9aXh__qForIPMsUDJ6WSerVqYXQhwZoNnkuiHeyQ-Md43j1f5GfAV5L2q_AOdPzcl6cMJRSdykN9niwLRz0fsHrPP91TO29SZ2MwCDQ9nYA3JD3w="        # 这里需要替换为你购买或者申请试用的RQData数据license

# 配置数据库
SETTINGS["database.name"] = "dolphindb"              # 可以根据自己的需求选择数据库，这里使用的是
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
    out_str += msg
    out_str += "\n"
    log_file.write(out_str)
    print(out_str)
    log_file.flush()

def main(csv_path):


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

            # 打印行数据（可替换为你的业务逻辑）
            # log_write(f"索引：{idx+1} 总数：{total} | 交易所：{exchange} "
            #           f"| 合约代码：{sec_id} | 上市日期：{listed_date} | 退市日期：{delisted_date}")

            vnpy_data_process(sec_id, exchange, listed_date, delisted_date, Interval.MINUTE)
            vnpy_data_process(sec_id, exchange, listed_date, delisted_date, Interval.HOUR)
            vnpy_data_process(sec_id, exchange, listed_date, delisted_date, Interval.DAILY)

            # if not check_loaded(symbol=sec_id, exchange=exchange, list_loaded=list_loaded):
            #     log_write(f"{sec_id}.{exchange} 未下载")

def check_loaded(symbol, exchange, list_loaded):
    for row in list_loaded:
        if row.symbol == symbol and row.exchange.value == exchange.value:
            return True

def vnpy_data_process(symbol, exchange, start, end, interval):
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
        bars: list[BarData] = datafeed.query_bar_history(req)
        log_write(f"下载数据成功：{symbol}.{exchange}，周期：{interval}，总数据量：{len(bars)}")
        # 如果下载成功则保存
        if bars:
            database.save_bar_data(bars)
            log_write(f"保存数据成功：{symbol}.{exchange}，周期：{interval}，总数据量：{len(bars)}")
        # 否则失败则打印信息
        else:
            log_write(f"数据为空：{symbol}.{exchange}")
    except Exception as e:
        log_write(f"========下载数据异常：{symbol}.{exchange}")
        log_write(e)


if __name__ == "__main__":

    main(r'I:\QUANT\QuantData\DataFeedEx\juejin\data\contracts_csv_202601')
    pass


