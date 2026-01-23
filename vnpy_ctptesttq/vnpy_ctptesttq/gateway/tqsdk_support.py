import asyncio
from threading import Thread
from typing import Dict
from tqsdk import TqApi, TqAuth
from tqsdk.objs import Quote

from common.account.tq_account import tq_auth
from common.tq_contracts_dict import TqContractsDict
from common.vnpy_time import get_now, datetime_format

from vnpy.trader.object import TickData, ContractData
from vnpy.trader.gateway import BaseGateway
from .ctptesttq_gateway import symbol_contract_map, adjust_price, EXCHANGE_CTP2VT


class TqsdkSupport:

    def __init__(self, gateway: BaseGateway) -> None:
        self.subscribed: Dict[str, object] = {}  # 合约行情对象字典
        self.gateway: BaseGateway = gateway  # vnpy网关
        self.gateway_name = gateway.gateway_name
        # 初始化天勤量化api
        self.api = TqApi(auth=tq_auth)

        self.contracts_obj = TqContractsDict(self.api)
        self.contracts = None
        self.contracts = self.contracts_obj.get_contracts()
        # 线程控制标志
        self._running = False
        self._thread = None
        self.start()

        # 严重问题，在这里获取数据，收到线程中 await_update影响，获取不到数据
        # 应该将线程改为协程模式
        # self.contracts = self.contracts_obj.get_contracts()


    # def write_log(self, msg: str, source: str = "MainEngine") -> None:
    #     """
    #     Put log event with specific message.
    #     """
    #     log: LogData = LogData(msg=msg, gateway_name=source)
    #     event: Event = Event(EVENT_LOG, log)
    #     self.event_engine.put(event)

    ##########################################
    # 业务处理部分
    ##########################################
    def subscribe_symbol(self, exchange: str, symbol: str) -> None:
        """
        订阅单个合约

        Args:
            symbol: 合约代码
        """
        # 拼装订阅合约
        instrument = f'{exchange}.{symbol}'


        from .ctptesttq_gateway import CtptesttqGateway
        tq_gateway: CtptesttqGateway = self.gateway
        # CTP交易服务已登录
        if tq_gateway.td_api.login_status:
            # 先做参数校验，不然会出错退出
            contract: ContractData = symbol_contract_map.get(symbol, None)
            if not contract:
                print("订阅合约不存在")
                return
        else:
            #print("未登录")
            # self.contracts = self.contracts_obj.get_contracts()
            contract = self.contracts[instrument]
            if not contract:
                print("订阅合约不存在")
                return



        # 判断是否已订阅，如已订阅，直接返回
        if instrument in self.subscribed:
            print(f"已添加订阅: {instrument}，无需再次订阅")
            return

        try:
            print(f"正在订阅合约: {instrument}")
            quote: Quote = self.api.get_quote(instrument)
            self.subscribed[instrument] = quote
            print(f"合约订阅成功: {instrument}")

            # 立即获取一次初始行情
            # initial_tick = self._convert_quote_to_tick(quote, symbol)
            # self._on_tick(initial_tick)

        except Exception as e:
            print(f"订阅合约失败 {symbol}: {e}")

    def unsubscribe_symbol(self, symbol: str):
        """
        取消订阅单个合约

        Args:
            symbol: 合约代码
        """

        # 判断合约是否在字典中
        if symbol in self.subscribed:
            try:
                # TqApi没有显式的取消订阅方法
                # 直接从字典中移除即可
                self.subscribed.pop(symbol, None)
                print(f"已取消合约订阅: {symbol}")
            except Exception as e:
                print(f"取消订阅失败 {symbol}: {e}")


    def onRtnDepthMarketData(self, symbol: str, quote: Quote) -> None:

        """行情数据推送"""
        # 过滤没有时间戳的异常行情数据
        if not quote.datetime:
            return

        exchange, _, symbol = quote.instrument_id.partition('.')

        # 对大商所的交易日字段取本地日期
        # if exchange == Exchange.DCE:
        #     # self.current_date: str = datetime.now().strftime("%Y%m%d")
        #     date_str: str = self.current_date
        # else:
        #   date_str = date_format_shorten(quote.datetime)


        dt = datetime_format(quote.datetime)


        tick: TickData = TickData(
            symbol=symbol,
            exchange=EXCHANGE_CTP2VT[exchange],
            datetime=dt,
            name=quote.instrument_name,
            volume=quote.volume,
            turnover=0, # 天勤量化里没有
            open_interest=quote.open_interest,
            last_price=adjust_price(quote.last_price),
            limit_up=quote.upper_limit,
            limit_down=quote.lower_limit,
            open_price=adjust_price(quote.open),
            high_price=adjust_price(quote.highest),
            low_price=adjust_price(quote.lowest),
            pre_close=adjust_price(quote.pre_close),
            bid_price_1=adjust_price(quote.bid_price1),
            ask_price_1=adjust_price(quote.ask_price1),
            bid_volume_1=quote.bid_volume1,
            ask_volume_1=quote.ask_volume1,
            gateway_name=self.gateway_name
        )

        if quote.bid_volume2 or quote.ask_volume2:
            tick.bid_price_2 = adjust_price(quote.bid_price2)
            tick.bid_price_3 = adjust_price(quote.bid_price3)
            tick.bid_price_4 = adjust_price(quote.bid_price4)
            tick.bid_price_5 = adjust_price(quote.bid_price5)

            tick.ask_price_2 = adjust_price(quote.ask_price2)
            tick.ask_price_3 = adjust_price(quote.ask_price3)
            tick.ask_price_4 = adjust_price(quote.ask_price4)
            tick.ask_price_5 = adjust_price(quote.ask_price5)

            tick.bid_volume_2 = quote.bid_volume2
            tick.bid_volume_3 = quote.bid_volume3
            tick.bid_volume_4 = quote.bid_volume4
            tick.bid_volume_5 = quote.bid_volume5

            tick.ask_volume_2 = quote.ask_volume2
            tick.ask_volume_3 = quote.ask_volume3
            tick.ask_volume_4 = quote.ask_volume4
            tick.ask_volume_5 = quote.ask_volume5

        self.gateway.on_tick(tick)



    ##########################################
    # 线程处理部分-----这里应该做异步线程
    ##########################################
    def _quote_loop(self):
        """
        行情数据获取主循环（线程函数）
        """
        print("行情数据线程开始运行")
        try:
            while self._running:
                # 阻塞等待更新
                self.api.wait_update()
                local_time = get_now()
                for symbol, quote in self.subscribed.items():
                    # 示例：移除值为偶数的键值对
                    # print(f"{local_time} symbol: {symbol} quote: {quote}")
                    self.onRtnDepthMarketData(symbol, quote)

        except Exception as e:
            print(f"行情线程发生错误: {e}")
            import traceback
            traceback.print_exc()

        finally:
            print("行情数据线程结束")
            # 清理资源
            self._cleanup()

    def start(self):
        """启动行情数据线程"""
        if self._running:
            print("tqsdk行情线程已在运行中")
            return

        self._running = True
        self._thread = Thread(target=self._quote_loop, name="TqsdkQuoteThread")
        self._thread.daemon = True
        self._thread.start()
        print("tqsdk行情线程已启动")

    def stop(self):
        """停止行情数据线程"""
        if not self._running:
            return

        print("正在停止行情线程...")
        self._running = False
        asyncio.sleep(0.5)
        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                print("警告: 行情线程未能正常结束")
            else:
                print("行情线程已停止")

        # 关闭API连接
        self._close_api()
    def _close_api(self):
        """关闭API连接"""
        try:
            if hasattr(self.api, 'close'):
                self.api.close()
                print("API连接已关闭")
        except Exception as e:
            print(f"关闭API连接时发生错误: {e}")

    def _cleanup(self):
        """清理资源"""
        print("正在清理资源...")
        self.subscribed.clear()
        self._close_api()
        print("资源清理完成")

    def __del__(self):
        """析构函数"""
        try:
            self.stop()
        except:
            pass

# 使用示例
# if __name__ == "__main__":
#     # 创建一个简单的网关模拟类用于测试
#     # class MockGateway:
#     #     def on_tick(self, tick):
#     #         print(f"收到行情: {tick.symbol}, 最新价: {tick.last_price}, "
#     #               f"买一价: {tick.bid_price_1}, 卖一价: {tick.ask_price_1}")
#
#     # 测试代码
#     event_engine = EventEngine()
#     gateway = BaseGateway(event_engine, "测试网关")
#     tqsdk = TqsdkSupport(gateway)
#
#     # 启动服务
#     tqsdk.start()
#
#     # 订阅合约
#     tqsdk.subscribe_symbol("SHFE.rb2401")  # 螺纹钢
#     tqsdk.subscribe_symbol("DCE.i2401")  # 铁矿石
#
#     try:
#         # 运行一段时间
#         print("程序运行中，按Ctrl+C停止...")
#         for i in range(30):  # 运行30秒
#             sleep(10)
#             if i == 10:
#                 # 10秒后取消一个订阅
#                 tqsdk.unsubscribe_symbol("DCE.i2401")
#     except KeyboardInterrupt:
#         print("收到停止信号")
#     finally:
#         # 停止服务
#         tqsdk.stop()