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
        # asyncio.sleep(1)
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

# 处理商品: IC
# 中证500期货主力合约
# 2026 - 01 - 23
# 12: 49:26 - INFO - 通知: 与
# wss: // free - api.shinnytech.com / t / nfmd / front / mobile
# 的网络连接已建立
# 行情数据线程开始运行
# 行情线程发生错误: 不能在协程中调用
# wait_update, 如需在协程中等待业务数据更新请使用
# register_update_notify
# 行情数据线程结束
# 正在清理资源...
# 关闭API连接时发生错误: 不能在协程中调用
# close, 如需关闭
# api
# 实例需在
# wait_update
# 返回后再关闭
# 资源清理完成
# Traceback(most
# recent
# call
# last):
# File
# "D:\02.QUANT\FrameWork\vnpy_zzfoo01\vnpy_ctptesttq\vnpy_ctptesttq\gateway\tqsdk_support.py", line
# 212, in _quote_loop_async
# self.api.wait_update()
#
#
# File
# "D:\02.DEV\miniconda3\envs\tqsdk-312\Lib\site-packages\tqsdk\api.py", line
# 1903, in wait_update
# raise Exception("不能在协程中调用 wait_update, 如需在协程中等待业务数据更新请使用 register_update_notify")
# Exception: 不能在协程中调用
# wait_update, 如需在协程中等待业务数据更新请使用
# register_update_notify
    ##########################################
    # 线程处理部分-----这里应该做异步线程
    # async 和 函数里的 await 是组合使用的
    ##########################################
    def _run_event_loop(self):
        """在新线程中运行事件循环"""
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # 创建并运行协程任务
            self._quote_task = loop.create_task(self._quote_loop_async())
            loop.run_forever()
        except Exception as e:
            print(f"事件循环发生错误: {e}")
        finally:
            loop.close()

    async def _quote_loop_async(self):
        """
        协程版本的行情数据获取主循环
        """

        loop = asyncio.get_event_loop()
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
        #
        #
        # try:
        #     while self._running:
        #         try:
        #             # 异步等待API更新（使用线程池包装同步API）
        #             await self._async_wait_update(timeout=1.0)
        #
        #             # 获取当前时间
        #             local_time = self._get_now()
        #
        #             # 并发处理所有订阅的合约
        #             tasks = []
        #             for symbol, quote in list(self.subscribed.items()):
        #                 if quote:  # 检查quote是否有效
        #                     task = self._process_quote_async(symbol, quote, local_time)
        #                     tasks.append(task)
        #
        #             # 并发执行，设置超时避免阻塞太久
        #             if tasks:
        #                 await asyncio.wait_for(
        #                     asyncio.gather(*tasks, return_exceptions=True),
        #                     timeout=5.0
        #                 )
        #
        #             # 短暂休眠，避免CPU占用过高
        #             await asyncio.sleep(0.001)
        #
        #         except asyncio.TimeoutError:
        #             print("处理行情数据超时")
        #             continue
        #         except asyncio.CancelledError:
        #             print("协程被取消")
        #             break
        #         except Exception as e:
        #             print(f"协程循环发生错误: {e}")
        #             traceback.print_exc()
        #             # 等待1秒后继续
        #             await asyncio.sleep(1)
        #
        # except Exception as e:
        #     print(f"协程发生严重错误: {e}")
        #     traceback.print_exc()
        #
        # finally:
        #     print("行情数据协程结束")
        #     await self._async_cleanup()



    # def _quote_loop(self):
    #     """
    #     行情数据获取主循环（线程函数）
    #     """
    #     loop = asyncio.get_event_loop()
    #     print("行情数据线程开始运行")
    #     try:
    #         while self._running:
    #             # 阻塞等待更新
    #             self.api.wait_update()
    #             local_time = get_now()
    #             for symbol, quote in self.subscribed.items():
    #                 # 示例：移除值为偶数的键值对
    #                 # print(f"{local_time} symbol: {symbol} quote: {quote}")
    #                 self.onRtnDepthMarketData(symbol, quote)
    #
    #     except Exception as e:
    #         print(f"行情线程发生错误: {e}")
    #         import traceback
    #         traceback.print_exc()
    #
    #     finally:
    #         print("行情数据线程结束")
    #         # 清理资源
    #         self._cleanup()

    def start(self):
        """启动行情数据线程"""
        if self._running:
            print("tqsdk行情线程已在运行中")
            return

        self._running = True
        self._thread = Thread(target=self._run_event_loop, name="TqsdkQuoteThread")
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

# import asyncio
# from datetime import datetime
# import traceback
#
#
# class TqsdkSupport:
#
#     async def _quote_loop_async(self):
#         """
#         协程版本的行情数据获取主循环
#         """
#         print("行情数据协程开始运行")
#
#         try:
#             while True:
#                 try:
#                     # 异步等待API更新
#                     await self._async_wait_update()
#
#                     local_time = self._get_now()
#
#                     # 异步处理所有订阅的合约
#                     tasks = []
#                     for symbol, quote in self.subscribed.items():
#                         # 为每个合约创建异步任务
#                         task = self._async_on_rtn_depth_market_data(symbol, quote)
#                         tasks.append(task)
#
#                     # 并发执行所有任务
#                     if tasks:
#                         await asyncio.gather(*tasks, return_exceptions=True)
#
#                 except asyncio.CancelledError:
#                     print("协程被取消")
#                     break
#                 except Exception as e:
#                     print(f"协程循环发生错误: {e}")
#                     traceback.print_exc()
#                     # 短暂等待后继续
#                     await asyncio.sleep(1)
#
#         except Exception as e:
#             print(f"协程发生严重错误: {e}")
#             traceback.print_exc()
#
#         finally:
#             print("行情数据协程结束")
#             await self._async_cleanup()
#
#     async def _async_wait_update(self):
#         """
#         异步等待API更新
#         如果API没有异步版本，需要使用线程池
#         """
#         # 方法1：如果API有异步版本
#         # return await self.api.async_wait_update()
#
#         # 方法2：使用线程池包装同步API
#         loop = asyncio.get_event_loop()
#         return await loop.run_in_executor(None, self.api.wait_update)
#
#     async def _async_on_rtn_depth_market_data(self, symbol, quote):
#         """
#         异步处理行情数据
#         """
#         try:
#             # 方法1：如果onRtnDepthMarketData是异步的
#             # await self.onRtnDepthMarketData(symbol, quote)
#
#             # 方法2：使用线程池处理同步函数
#             loop = asyncio.get_event_loop()
#             await loop.run_in_executor(
#                 None,
#                 self.onRtnDepthMarketData,
#                 symbol,
#                 quote
#             )
#         except Exception as e:
#             print(f"处理行情数据时发生错误 {symbol}: {e}")
#
#     async def _async_cleanup(self):
#         """异步清理资源"""
#         print("正在异步清理资源...")
#         # 执行清理操作
#         if hasattr(self, 'api') and hasattr(self.api, 'close'):
#             loop = asyncio.get_event_loop()
#             await loop.run_in_executor(None, self.api.close)
#         print("资源清理完成")


# import asyncio
# import threading
# from datetime import datetime
# from concurrent.futures import ThreadPoolExecutor
# from typing import Dict, Any, Optional
# import traceback
#
#
# class AsyncTqsdkSupport:
#     """
#     支持协程的TqsdkSupport类
#     """
#
#     def __init__(self, gateway, max_workers: int = 10):
#         """
#         初始化
#
#         Args:
#             gateway: 网关实例
#             max_workers: 线程池最大工作线程数
#         """
#         self.gateway = gateway
#         self.subscribed: Dict[str, Any] = {}
#
#         # 线程池用于执行同步操作
#         self._executor = ThreadPoolExecutor(max_workers=max_workers)
#
#         # 协程任务
#         self._quote_task: Optional[asyncio.Task] = None
#         self._running = False
#
#         # 初始化API（假设是同步的）
#         # self.api = TqApi(auth=TqAuth("username", "password"))
#
#         print("AsyncTqsdkSupport初始化完成")
#
#     def start(self):
#         """启动协程"""
#         if self._running:
#             print("协程已在运行中")
#             return
#
#         self._running = True
#
#         # 在新线程中运行事件循环
#         self._event_loop_thread = threading.Thread(
#             target=self._run_event_loop,
#             name="AsyncEventLoopThread",
#             daemon=True
#         )
#         self._event_loop_thread.start()
#
#         print("协程已启动")
#
#     def _run_event_loop(self):
#         """在新线程中运行事件循环"""
#         # 创建新的事件循环
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
#
#         try:
#             # 创建并运行协程任务
#             self._quote_task = loop.create_task(self._quote_loop_async())
#             loop.run_forever()
#         except Exception as e:
#             print(f"事件循环发生错误: {e}")
#         finally:
#             loop.close()
#
#     async def _quote_loop_async(self):
#         """
#         协程版本的行情数据获取主循环
#         """
#         print("行情数据协程开始运行")
#
#         try:
#             while self._running:
#                 try:
#                     # 异步等待API更新（使用线程池包装同步API）
#                     await self._async_wait_update(timeout=1.0)
#
#                     # 获取当前时间
#                     local_time = self._get_now()
#
#                     # 并发处理所有订阅的合约
#                     tasks = []
#                     for symbol, quote in list(self.subscribed.items()):
#                         if quote:  # 检查quote是否有效
#                             task = self._process_quote_async(symbol, quote, local_time)
#                             tasks.append(task)
#
#                     # 并发执行，设置超时避免阻塞太久
#                     if tasks:
#                         await asyncio.wait_for(
#                             asyncio.gather(*tasks, return_exceptions=True),
#                             timeout=5.0
#                         )
#
#                     # 短暂休眠，避免CPU占用过高
#                     await asyncio.sleep(0.001)
#
#                 except asyncio.TimeoutError:
#                     print("处理行情数据超时")
#                     continue
#                 except asyncio.CancelledError:
#                     print("协程被取消")
#                     break
#                 except Exception as e:
#                     print(f"协程循环发生错误: {e}")
#                     traceback.print_exc()
#                     # 等待1秒后继续
#                     await asyncio.sleep(1)
#
#         except Exception as e:
#             print(f"协程发生严重错误: {e}")
#             traceback.print_exc()
#
#         finally:
#             print("行情数据协程结束")
#             await self._async_cleanup()
#
#     async def _async_wait_update(self, timeout: float = None):
#         """
#         异步等待API更新
#
#         Args:
#             timeout: 超时时间（秒）
#         """
#         loop = asyncio.get_event_loop()
#
#         # 使用线程池执行同步的wait_update
#         try:
#             if timeout:
#                 # 带超时的版本
#                 return await asyncio.wait_for(
#                     loop.run_in_executor(self._executor, self.api.wait_update),
#                     timeout=timeout
#                 )
#             else:
#                 # 不带超时的版本
#                 return await loop.run_in_executor(
#                     self._executor,
#                     self.api.wait_update
#                 )
#         except asyncio.TimeoutError:
#             return None  # 超时返回None
#
#     async def _process_quote_async(self, symbol: str, quote: Any, local_time: datetime):
#         """
#         异步处理单个合约行情
#
#         Args:
#             symbol: 合约代码
#             quote: 行情数据
#             local_time: 当前时间
#         """
#         try:
#             # 在事件循环中直接调用回调函数
#             # 如果回调函数是CPU密集型的，建议使用线程池
#             self.onRtnDepthMarketData(symbol, quote)
#
#             # 如果需要在线程池中执行（如果回调是阻塞的）
#             # loop = asyncio.get_event_loop()
#             # await loop.run_in_executor(
#             #     self._executor,
#             #     self.onRtnDepthMarketData,
#             #     symbol,
#             #     quote
#             # )
#
#         except Exception as e:
#             print(f"处理合约 {symbol} 行情时发生错误: {e}")
#
#     def onRtnDepthMarketData(self, symbol: str, quote: Any):
#         """
#         行情回调函数（同步版本）
#         重写此方法以处理行情数据
#
#         Args:
#             symbol: 合约代码
#             quote: 行情数据
#         """
#         # 这里是示例实现，实际使用时需要重写
#         print(f"收到行情: {symbol}, 最新价: {getattr(quote, 'last_price', 'N/A')}")
#
#         # 如果需要传递给gateway
#         if hasattr(self.gateway, 'on_tick'):
#             tick = self._convert_quote_to_tick(quote, symbol)
#             self.gateway.on_tick(tick)
#
#     def _convert_quote_to_tick(self, quote: Any, symbol: str):
#         """将quote转换为tick"""
#         # 这里是转换逻辑，根据实际情况实现
#         # from vnpy.trader.object import TickData
#         # tick = TickData(...)
#         # return tick
#         pass
#
#     def _get_now(self) -> datetime:
#         """获取当前时间"""
#         return datetime.now()
#
#     async def _async_cleanup(self):
#         """异步清理资源"""
#         print("正在异步清理资源...")
#
#         # 清理订阅
#         self.subscribed.clear()
#
#         # 关闭API
#         if hasattr(self, 'api') and hasattr(self.api, 'close'):
#             loop = asyncio.get_event_loop()
#             await loop.run_in_executor(self._executor, self.api.close)
#
#         # 关闭线程池
#         if hasattr(self, '_executor'):
#             self._executor.shutdown(wait=True)
#
#         print("资源清理完成")
#
#     def subscribe(self, symbol: str):
#         """订阅行情"""
#         if symbol not in self.subscribed:
#             # 获取quote对象
#             quote = self.api.get_quote(symbol)
#             self.subscribed[symbol] = quote
#             print(f"已订阅: {symbol}")
#
#     def unsubscribe(self, symbol: str):
#         """取消订阅"""
#         if symbol in self.subscribed:
#             del self.subscribed[symbol]
#             print(f"已取消订阅: {symbol}")
#
#     def stop(self):
#         """停止协程"""
#         if not self._running:
#             return
#
#         print("正在停止协程...")
#         self._running = False
#
#         # 取消协程任务
#         if self._quote_task:
#             self._quote_task.cancel()
#
#         # 等待事件循环线程结束
#         if hasattr(self, '_event_loop_thread') and self._event_loop_thread.is_alive():
#             self._event_loop_thread.join(timeout=5)
#
#         print("协程已停止")
#
#     def __del__(self):
#         """析构函数"""
#         self.stop()
