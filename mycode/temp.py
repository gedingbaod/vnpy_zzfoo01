import time
from threading import Thread
from typing import Dict, Set
from tqsdk import TqApi, TqAuth


class TqsdkSupport2:
    """天勤SDK支持类"""

    def __init__(self, gateway) -> None:
        """
        初始化TqsdkSupport

        Args:
            gateway: 网关实例，需要实现on_tick等方法
        """
        self.gateway = gateway
        self.subscribed: Set[str] = set()  # 已订阅的合约代码
        self.quotes: Dict[str, object] = {}  # 合约行情对象字典

        # 初始化TqApi连接
        self.api = TqApi(auth=TqAuth("gedingbaod", "3028023abc"))

        # 线程控制标志
        self._running = False
        self._thread = None

        print("TqsdkSupport初始化完成")

    def start(self):
        """启动行情数据线程"""
        if self._running:
            print("行情线程已在运行中")
            return

        self._running = True
        self._thread = Thread(target=self._quote_loop, name="TqsdkQuoteThread")
        self._thread.daemon = True
        self._thread.start()
        print("行情线程已启动")

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

    def subscribe(self, symbol: str):
        """
        订阅行情

        Args:
            symbol: 合约代码，如'shfe.rb2401'
        """
        if symbol not in self.subscribed:
            self.subscribed.add(symbol)
            print(f"已添加订阅: {symbol}")

    def unsubscribe(self, symbol: str):
        """
        取消订阅行情

        Args:
            symbol: 合约代码
        """
        if symbol in self.subscribed:
            self.subscribed.remove(symbol)
            print(f"已取消订阅: {symbol}")

    def _quote_loop(self):
        """
        行情数据获取主循环（线程函数）
        """
        print("行情数据线程开始运行")

        try:
            while self._running:
                # 检查是否有新的订阅请求
                new_subscriptions = self.subscribed - set(self.quotes.keys())
                if new_subscriptions:
                    for symbol in new_subscriptions:
                        self._subscribe_symbol(symbol)

                # 移除已取消订阅的合约
                canceled = set(self.quotes.keys()) - self.subscribed
                for symbol in canceled:
                    self._unsubscribe_symbol(symbol)

                # 等待API数据更新（非阻塞方式）
                # 设置较短的超时时间，以便及时响应停止信号
                self.api.wait_update(timeout=1)

                # 处理所有订阅合约的行情更新
                for symbol, quote in self.quotes.items():
                    if self.api.is_changing(quote):
                        tick = self._convert_quote_to_tick(quote, symbol)
                        self._on_tick(tick)

                # 添加短暂休眠，防止CPU占用过高
                time.sleep(0.01)

        except Exception as e:
            print(f"行情线程发生错误: {e}")
            import traceback
            traceback.print_exc()

        finally:
            print("行情数据线程结束")
            # 清理资源
            self._cleanup()

    def _subscribe_symbol(self, symbol: str):
        """
        订阅单个合约

        Args:
            symbol: 合约代码
        """
        try:
            print(f"正在订阅合约: {symbol}")
            quote = self.api.get_quote(symbol)
            self.quotes[symbol] = quote
            print(f"合约订阅成功: {symbol}")

            # 立即获取一次初始行情
            initial_tick = self._convert_quote_to_tick(quote, symbol)
            self._on_tick(initial_tick)

        except Exception as e:
            print(f"订阅合约失败 {symbol}: {e}")

    def _unsubscribe_symbol(self, symbol: str):
        """
        取消订阅单个合约

        Args:
            symbol: 合约代码
        """
        if symbol in self.quotes:
            try:
                # TqApi没有显式的取消订阅方法
                # 直接从字典中移除即可
                self.quotes.pop(symbol, None)
                print(f"已取消合约订阅: {symbol}")
            except Exception as e:
                print(f"取消订阅失败 {symbol}: {e}")

    def _on_tick(self, tick):
        """
        处理行情回调

        Args:
            tick: TickData对象
        """
        try:
            if hasattr(self.gateway, 'on_tick'):
                self.gateway.on_tick(tick)
            elif hasattr(self.gateway, 'process_tick_event'):
                self.gateway.process_tick_event(tick)
            else:
                print(f"收到行情数据，但gateway没有on_tick方法: {tick.symbol}")
        except Exception as e:
            print(f"处理行情回调时发生错误: {e}")

    def _convert_quote_to_tick(self, quote, symbol: str):
        """
        将TqApi的quote对象转换为TickData对象

        Args:
            quote: TqApi的quote对象
            symbol: 合约代码

        Returns:
            TickData对象
        """
        try:
            # 尝试导入vn.py的数据结构
            # 如果导入失败，使用简化版本
            try:
                from vnpy.trader.constant import Exchange
                from vnpy.trader.object import TickData
                has_vnpy = True
            except ImportError:
                has_vnpy = False
                # 创建一个简化的TickData类
                from dataclasses import dataclass
                from datetime import datetime

                @dataclass
                class SimpleTickData:
                    symbol: str = ""
                    exchange: str = ""
                    datetime: datetime = None
                    name: str = ""
                    last_price: float = 0.0
                    volume: int = 0
                    open_interest: float = 0.0
                    open_price: float = 0.0
                    high_price: float = 0.0
                    low_price: float = 0.0
                    pre_close: float = 0.0
                    limit_up: float = 0.0
                    limit_down: float = 0.0
                    bid_price_1: float = 0.0
                    bid_volume_1: int = 0
                    ask_price_1: float = 0.0
                    ask_volume_1: int = 0
                    bid_price_2: float = 0.0
                    bid_volume_2: int = 0
                    ask_price_2: float = 0.0
                    ask_volume_2: int = 0
                    bid_price_3: float = 0.0
                    bid_volume_3: int = 0
                    ask_price_3: float = 0.0
                    ask_volume_3: int = 0
                    bid_price_4: float = 0.0
                    bid_volume_4: int = 0
                    ask_price_4: float = 0.0
                    ask_volume_4: int = 0
                    bid_price_5: float = 0.0
                    bid_volume_5: int = 0
                    ask_price_5: float = 0.0
                    ask_volume_5: int = 0

                TickData = SimpleTickData

            # 获取交易所信息
            exchange = self._get_exchange_from_symbol(symbol)

            # 创建TickData对象
            tick = TickData()
            tick.symbol = symbol
            tick.exchange = exchange if has_vnpy else exchange.value if hasattr(exchange, 'value') else exchange
            tick.datetime = getattr(quote, 'datetime', None)
            tick.name = getattr(quote, 'instrument_name', symbol)
            tick.last_price = getattr(quote, 'last_price', 0.0)
            tick.volume = int(getattr(quote, 'volume', 0))
            tick.open_interest = getattr(quote, 'open_interest', 0.0)
            tick.open_price = getattr(quote, 'open', 0.0)
            tick.high_price = getattr(quote, 'high', 0.0)
            tick.low_price = getattr(quote, 'low', 0.0)
            tick.pre_close = getattr(quote, 'pre_close', 0.0)
            tick.limit_up = getattr(quote, 'upper_limit', 0.0)
            tick.limit_down = getattr(quote, 'lower_limit', 0.0)

            # 买卖盘数据
            tick.bid_price_1 = getattr(quote, 'bid_price1', 0.0)
            tick.bid_volume_1 = int(getattr(quote, 'bid_volume1', 0))
            tick.ask_price_1 = getattr(quote, 'ask_price1', 0.0)
            tick.ask_volume_1 = int(getattr(quote, 'ask_volume1', 0))

            # 更多档位数据
            for i in range(2, 6):
                bid_price = getattr(quote, f'bid_price{i}', 0.0)
                bid_volume = getattr(quote, f'bid_volume{i}', 0)
                ask_price = getattr(quote, f'ask_price{i}', 0.0)
                ask_volume = getattr(quote, f'ask_volume{i}', 0)

                setattr(tick, f'bid_price_{i}', bid_price)
                setattr(tick, f'bid_volume_{i}', int(bid_volume))
                setattr(tick, f'ask_price_{i}', ask_price)
                setattr(tick, f'ask_volume_{i}', int(ask_volume))

            return tick

        except Exception as e:
            print(f"转换行情数据时发生错误: {e}")
            # 返回一个基本的TickData对象
            from dataclasses import dataclass
            from datetime import datetime

            @dataclass
            class BasicTickData:
                symbol: str = ""
                exchange: str = ""
                datetime: datetime = None
                name: str = ""
                last_price: float = 0.0
                volume: int = 0
                bid_price_1: float = 0.0
                bid_volume_1: int = 0
                ask_price_1: float = 0.0
                ask_volume_1: int = 0

            tick = BasicTickData(symbol=symbol, datetime=datetime.now())
            return tick

    def _get_exchange_from_symbol(self, symbol: str):
        """
        从合约代码中提取交易所信息

        Args:
            symbol: 合约代码，如'shfe.rb2401'或'DCE.i2401'

        Returns:
            交易所枚举或字符串
        """
        # 常见交易所映射
        exchange_map = {
            'shfe': 'SHFE',  # 上期所
            'cffex': 'CFFEX',  # 中金所
            'dce': 'DCE',  # 大商所
            'czce': 'CZCE',  # 郑商所
            'ine': 'INE',  # 能源中心
        }

        # 提取交易所代码（通常为前4个字符）
        if '.' in symbol:
            exchange_code = symbol.split('.')[0].lower()
            return exchange_map.get(exchange_code, 'UNKNOWN')
        else:
            return 'UNKNOWN'

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
        self.quotes.clear()
        self._close_api()
        print("资源清理完成")

    def __del__(self):
        """析构函数"""
        try:
            self.stop()
        except:
            pass


# 使用示例
if __name__ == "__main__":
    # 创建一个简单的网关模拟类用于测试
    class MockGateway:
        def on_tick(self, tick):
            print(f"收到行情: {tick.symbol}, 最新价: {tick.last_price}, "
                  f"买一价: {tick.bid_price_1}, 卖一价: {tick.ask_price_1}")


    # 测试代码
    gateway = MockGateway()
    tqsdk = TqsdkSupport2(gateway)

    # 启动服务
    tqsdk.start()

    # 订阅合约
    tqsdk.subscribe("SHFE.rb2401")  # 螺纹钢
    tqsdk.subscribe("DCE.i2401")  # 铁矿石

    try:
        # 运行一段时间
        print("程序运行中，按Ctrl+C停止...")
        for i in range(30):  # 运行30秒
            time.sleep(1)
            if i == 10:
                # 10秒后取消一个订阅
                tqsdk.unsubscribe("DCE.i2401")
    except KeyboardInterrupt:
        print("收到停止信号")
    finally:
        # 停止服务
        tqsdk.stop()