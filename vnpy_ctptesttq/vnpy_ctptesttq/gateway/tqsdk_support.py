from datetime import time
from threading import Thread
from time import sleep
from typing import Dict, Set
from tqsdk import TqApi, TqAuth
from vnpy.event import EventEngine
from vnpy.trader.gateway import BaseGateway


class TqsdkSupport:

    def __init__(self, gateway: BaseGateway) -> None:
        self.subscribed: Dict[str, object] = {}  # 合约行情对象字典
        self.gateway: BaseGateway = gateway  # vnpy网关
        # 初始化天勤量化api
        self.api = TqApi(auth=TqAuth("gedingbaod", "3028023abc"))

        # 线程控制标志
        self._running = False
        self._thread = None

    ##########################################
    # 业务处理部分
    ##########################################
    def subscribe_symbol(self, symbol: str) -> None:
        """
        订阅单个合约

        Args:
            symbol: 合约代码
        """
        # 判断是否已订阅，如已订阅，直接返回
        if symbol in self.subscribed:
            print(f"已添加订阅: {symbol}，无需再次订阅")
            return

        try:
            print(f"正在订阅合约: {symbol}")
            quote = self.api.get_quote(symbol)
            self.subscribed[symbol] = quote
            print(f"合约订阅成功: {symbol}")

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

    ##########################################
    # 线程处理部分
    ##########################################
    def _quote_loop(self):
        """
        行情数据获取主循环（线程函数）
        """
        print("行情数据线程开始运行")
        try:
            while True:
                # 阻塞等待更新
                self.api.wait_update()
                for symbol, quote in self.subscribed.items():
                    # 示例：移除值为偶数的键值对
                    print(f"near_quote：{quote}")

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