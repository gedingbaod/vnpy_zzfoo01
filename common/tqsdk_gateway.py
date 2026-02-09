"""
TQSDK行情API通用模块
可用于各种gateway的TQSDK行情接入
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta, time
from time import sleep
from threading import Thread
from typing import Any, Union, Optional

import pandas as pd
from tqsdk.objs import Quote
from tqsdk import TqApi, TqAuth

from common.func_magic import print_msg_with_time_once
from common.gateway_tq import BaseGatewayTq
from common.risk_manager import RiskManager
from common.strategy_spread import AgSpreadStrategy, EVENT_UPDATE_QUOTE, SnSpreadStrategy, NiSpreadStrategy
from vnpy.event import Event
from vnpy.trader.constant import (
    Direction,
    Offset,
    Exchange,
    OrderType,
    Status,
)
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    ContractData,
)
from vnpy.trader.utility import get_folder_path, ZoneInfo

try:
    from common.account.tq_account import tq_auth
    from common.vnpy_time import datetime_format, get_now_str

    TQ_AUTH_AVAILABLE = True
except ImportError:
    TQ_AUTH_AVAILABLE = False
    tq_auth = None

# 交易所映射（需要从主程序中导入）
EXCHANGE_TTS2VT: dict[str, Exchange] = {
    "CFFEX": Exchange.CFFEX,
    "SHFE": Exchange.SHFE,
    "CZCE": Exchange.CZCE,
    "DCE": Exchange.DCE,
    "GFEX": Exchange.GFEX,
    "INE": Exchange.INE,
    "SSE": Exchange.SSE,
    "SZSE": Exchange.SZSE,
    "NASD": Exchange.NASDAQ,
    "NYSE": Exchange.NYSE,
    "HKEX": Exchange.SEHK,
}

# 其他常量
MAX_FLOAT = sys.float_info.max
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def adjust_price(price: float) -> float:
    """将异常的浮点数最大值（MAX_FLOAT）数据调整为0"""
    if price == MAX_FLOAT:
        price = 0
    return price


class TqSdkMdApi:
    """
    TQSDK行情API通用类

    用于从tqsdk订阅期货合约行情，可复用于各种gateway

    使用方法：
    1. 初始化并连接
    2. 订阅行情
    3. 在_run函数中处理策略逻辑
    """

    def __init__(self, gateway: BaseGatewayTq, auth: TqAuth = None) -> None:
        """
        构造函数

        Parameters
        ----------
        gateway : BaseGateway
            Gateway实例，用于访问交易功能和日志输出
        auth : TqAuth, optional
            TQSDK认证对象，如果为None则使用tq_account中的tq_auth
        """
        self.gateway: BaseGatewayTq = gateway
        self.gateway_name: str = gateway.gateway_name

        self.api: TqApi | None = None
        self.active: bool = False
        self.thread: Thread | None = None

        # 在未连接前，还无法订阅行情，此处保存要订阅的数据，连接之后会再调用一次订阅
        self.subscribed: set = set()
        self.quotes: dict[str, Quote] = defaultdict()  # 保存行情引用 {symbol: quote}
        self.klines: dict[str, pd.DataFrame] = defaultdict()  # 保存行情引用 {symbol: kline}

        # 测试下单相关，代码已经被注释了
        self.order_placed: bool = False  # 是否已经下过单
        self.order_ids: list[str] = []  # 记录订单ID
        self.traded_vt_orderids: set = set()  # 已成交的订单ID集合

        # 跨期套利策略（使用策略类管理）
        # self.spread_strategy = AgSpreadStrategy(gateway)
        self.spread_strategy = NiSpreadStrategy(gateway)

        # 创建风险管理器
        self.risk_manager = RiskManager(strategy=self.spread_strategy)

        # TQSDK认证
        self.auth = auth if auth else (tq_auth if TQ_AUTH_AVAILABLE else None)

    def connect(self) -> None:
        """连接TQSDK"""
        try:
            # 初始化TQSDK API
            if self.auth:
                self.api = TqApi(auth=self.auth)
            else:
                self.api = TqApi()

            self.gateway.write_log("TQSDK行情连接成功")



            # 订阅合约之后，启动行情接收线程
            start_time_str = get_now_str()
            self.gateway.write_log(f"启动TQSDK行情线程，时间{start_time_str}")
            self.active = True
            self.thread = Thread(target=self._run, name="TqsdkQuoteLoop")
            self.thread.start()


            # 连接TqApi后即可把之前已经订阅的合约，真正开始订阅
            # 放在线程启动之后，可以接受一次行情数据，否则接受不到
            for symbol in self.subscribed:
                # 在SpreadStrategy已经订阅了两个合约，这里真正订阅
                self._subscribe_symbol(symbol)
            event: Event = Event(type=EVENT_UPDATE_QUOTE)
            self.gateway.event_engine.put(event)


            self.risk_manager.start()

        except Exception as e:
            self.gateway.write_log(f"TQSDK行情连接失败：{str(e)}")

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        symbol: str = req.symbol

        # 过滤重复的订阅
        if symbol in self.subscribed:
            return

        # 订阅逻辑分已连接和未连接，刚启动时未连接
        # 已连接情况，订阅合约，加入订阅列表
        # 未连接情况，只加入订阅列表，在连接后会再调用一次订阅
        if self.active:
            self._subscribe_symbol(symbol)
        self.subscribed.add(symbol)

    def _subscribe_symbol(self, symbol: str) -> None:
        """订阅单个合约"""
        # 过滤重复的订阅
        if symbol in self.quotes:
            self.gateway.write_log(f"{symbol}已添加订阅，无需再次订阅")
            return

        # 从全局合约映射中获取合约信息
        contract: ContractData | None = self.gateway.symbol_contract_map_tqsdk.get(symbol, None)
        if not contract:
            self.gateway.write_log(f"未找到合约{symbol}或行情未连接")
            return

        # 构造tqsdk格式的合约代码
        tq_symbol: str = f"{contract.exchange.value}.{symbol}"

        try:
            # 获取行情引用（自动订阅）
            quote = self.api.get_quote(tq_symbol)
            kline = self.api.get_kline_serial(tq_symbol, self.spread_strategy.klines_duration, self.spread_strategy.klines_windows * 2)

            # 保存行情引用
            self.quotes[symbol] = quote
            self.klines[symbol] = kline
            self.gateway.write_log(f"TQSDK订阅行情成功：{tq_symbol}")

        except Exception as e:
            self.gateway.write_log(f"TQSDK订阅行情失败 [{tq_symbol}]：{str(e)}")

    def _run(self) -> None:
        start_time_str = get_now_str()
        self.gateway.write_log(f"TQSDK行情线程启动，时间{start_time_str}")

        """行情接收线程"""
        while self.active:
            try:
                # 等待行情推送
                self.api.wait_update()

                print_msg_with_time_once("第一次接受到数据")

                # 检查是否有跨期合约的行情，执行跨期套利策略
                self.spread_strategy.check_and_run(self.quotes, self.klines)

                # 处理所有已订阅合约的行情
                for symbol, quote in self.quotes.items():
                    # 处理行情数据（显示在界面上）
                    self._process_tick(symbol, quote)

            # except KeyboardInterrupt:
            #     print("主线程捕获到中断，正在退出...")
            except Exception as e:
                if self.active:
                    self.gateway.write_log(f"TQSDK行情处理异常：{str(e)}")
                import traceback
                traceback.print_exc()

        # 退出循环后关闭API
        print("wait_update循环退出")

    def _process_tick(self, symbol: str, quote: Quote) -> None:
        """处理单个合约的行情数据"""
        if not self.api:
            return

        try:
            # 检查行情数据是否有效
            if not quote.datetime:
                return

            exchange, _, symbol_name = quote.instrument_id.partition('.')

            dt = datetime_format(quote.datetime)

            # 构造TickData对象
            tick: TickData = TickData(
                symbol=symbol_name,
                exchange=EXCHANGE_TTS2VT.get(exchange, Exchange.SHFE),
                datetime=dt,
                name=quote.instrument_name,
                volume=quote.volume,
                turnover=quote.amount,
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

            # 如果有五档行情，也设置
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

            # 推送行情数据
            self.gateway.on_tick(tick)

        except Exception as e:
            self.gateway.write_log(f"TQSDK行情数据转换异常 [{symbol}]：{str(e)}")


    # def on_order_status_update(self, vt_orderid: str, status: Status) -> None:
    #     """
    #     订单状态更新回调（从TdApi调用）
    #
    #     将订单状态更新转发给策略处理
    #     """
    #     # 转发给策略处理
    #     self.spread_strategy.on_order_status_update(vt_orderid, status)

    def close(self) -> None:
        """关闭连接"""
        self.active = False

        # 等待线程结束
        if self.thread and self.thread.is_alive():
            # 为了触发await_update解除阻塞，所以订阅了一个行情连接
            _ = self.api.get_quote('ag2610')
            self.thread.join(timeout=5)
            self.thread = None

        # 关闭API
        if self.api:
            try:
                self.api.close()
            except Exception as e:
                print(f"api关闭失败: {e}")
                import traceback
                traceback.print_exc()
            self.api = None

        # 清空数据
        self.quotes.clear()
        self.gateway.write_log("TQSDK行情连接已关闭")
        print("TQSDK行情连接已关闭")

        self.risk_manager.stop()

    # ==================== 测试相关方法 ====================
    # def _order_ag2604(self, symbol: str, quote: Quote) -> None:
    #     """测试ag2604下单功能"""
    #     # 只在ag2604上操作，且只下单一次
    #     if symbol != "ag2604" or self.order_placed:
    #         return
    #
    #     # 标记已下单，避免重复下单
    #     self.order_placed = True
    #
    #     try:
    #         # 获取合约信息
    #         contract: ContractData | None = self.gateway.symbol_contract_map_tqsdk.get(symbol, None)
    #         if not contract:
    #             self.gateway.write_log(f"未找到合约{symbol}的合约信息")
    #             return
    #
    #         # 获取当前价格
    #         current_price = quote.last_price
    #         if current_price <= 0:
    #             self.gateway.write_log(f"获取{symbol}当前价格失败")
    #             return
    #
    #         self.gateway.write_log(f"开始测试下单 {symbol}，当前价格: {current_price}")
    #
    #         # 下单1：买单（开仓）
    #         req_buy: OrderRequest = OrderRequest(
    #             symbol=symbol,
    #             exchange=contract.exchange,
    #             direction=Direction.LONG,
    #             type=OrderType.MARKET,
    #             volume=1,
    #             price=0,  # 市价单价格设为0
    #             offset=Offset.OPEN,
    #             reference="test_buy"
    #         )
    #         order_id_buy: str = self.gateway.send_order(req_buy)
    #
    #         if order_id_buy:
    #             self.order_ids.append(order_id_buy)
    #             self.gateway.write_log(f"买单已发送: {order_id_buy}")
    #         else:
    #             self.gateway.write_log("买单发送失败")
    #             return
    #
    #         # 下单2：卖单（开仓）
    #         req_sell: OrderRequest = OrderRequest(
    #             symbol=symbol,
    #             exchange=contract.exchange,
    #             direction=Direction.SHORT,
    #             type=OrderType.MARKET,
    #             volume=1,
    #             price=0,  # 市价单价格设为0
    #             offset=Offset.OPEN,
    #             reference="test_sell"
    #         )
    #         order_id_sell: str = self.gateway.send_order(req_sell)
    #
    #         if order_id_sell:
    #             self.order_ids.append(order_id_sell)
    #             self.gateway.write_log(f"卖单已发送: {order_id_sell}")
    #         else:
    #             self.gateway.write_log("卖单发送失败")
    #             return
    #
    #         # 启动定时器，10秒后平仓
    #         timer = Thread(target=self._delay_close_positions, args=(symbol, contract,))
    #         timer.daemon = True
    #         timer.start()
    #         self.gateway.write_log("已启动10秒定时器，届时将自动平仓")
    #
    #     except Exception as e:
    #         self.gateway.write_log(f"下单异常: {str(e)}")
    #
    # def _delay_close_positions(self, symbol: str, contract: ContractData) -> None:
    #     """延迟平仓的线程函数"""
    #     sleep(10)  # 等待10秒
    #     self.gateway.write_log("10秒已到，开始平仓...")
    #     self._close_ag2604_positions(symbol, contract)
    #
    # def _close_ag2604_positions(self, symbol: str, contract: ContractData) -> None:
    #     """智能平仓ag2604的持仓，区分上期所今仓昨仓"""
    #     try:
    #         # 先查询持仓，获取持仓详情
    #         self.gateway.query_position()
    #
    #         # 这里有update_pos_condition同步问题，所以加锁
    #         with self.gateway.update_pos_condition:
    #             self.gateway.update_pos_condition.wait()
    #
    #             # 获取多单持仓（Direction.LONG）
    #             long_positions = [pos for pos in self.gateway.positions_for_tqsdk.values()
    #                               if pos.symbol == symbol and pos.direction == Direction.LONG and pos.volume > 0]
    #
    #             # 获取空单持仓（Direction.SHORT）
    #             short_positions = [pos for pos in self.gateway.positions_for_tqsdk.values()
    #                                if pos.symbol == symbol and pos.direction == Direction.SHORT and pos.volume > 0]
    #             # 临时变量，用完即刻清空
    #             self.gateway.positions_for_tqsdk.clear()
    #
    #         # 平多单
    #         for pos in long_positions:
    #             close_volume = pos.volume
    #             if close_volume <= 0:
    #                 continue
    #
    #             # 判断平仓offset：上期所需要区分今昨仓
    #             if contract.exchange in [Exchange.SHFE, Exchange.INE]:
    #                 # 如果有昨仓，先平昨仓；剩下的平今仓
    #                 if pos.yd_volume > 0:
    #                     close_yd_volume = min(pos.yd_volume, close_volume)
    #                     self._send_close_order(symbol, contract, Direction.SHORT,
    #                                           close_yd_volume, Offset.CLOSEYESTERDAY)
    #                     close_volume -= close_yd_volume
    #
    #                 if close_volume > 0:
    #                     self._send_close_order(symbol, contract, Direction.SHORT,
    #                                           close_volume, Offset.CLOSETODAY)
    #             else:
    #                 # 非上期所，直接平仓
    #                 self._send_close_order(symbol, contract, Direction.SHORT,
    #                                       close_volume, Offset.CLOSE)
    #
    #         # 平空单
    #         for pos in short_positions:
    #             close_volume = pos.volume
    #             if close_volume <= 0:
    #                 continue
    #
    #             # 判断平仓offset：上期所需要区分今昨仓
    #             if contract.exchange in [Exchange.SHFE, Exchange.INE]:
    #                 # 如果有昨仓，先平昨仓；剩下的平今仓
    #                 if pos.yd_volume > 0:
    #                     close_yd_volume = min(pos.yd_volume, close_volume)
    #                     self._send_close_order(symbol, contract, Direction.LONG,
    #                                           close_yd_volume, Offset.CLOSEYESTERDAY)
    #                     close_volume -= close_yd_volume
    #
    #                 if close_volume > 0:
    #                     self._send_close_order(symbol, contract, Direction.LONG,
    #                                           close_volume, Offset.CLOSETODAY)
    #             else:
    #                 # 非上期所，直接平仓
    #                 self._send_close_order(symbol, contract, Direction.LONG,
    #                                       close_volume, Offset.CLOSE)
    #
    #         # 清空订单记录
    #         self.order_ids.clear()
    #
    #     except Exception as e:
    #         self.gateway.write_log(f"平仓异常: {str(e)}")

    def _send_close_order(self, symbol: str, contract: ContractData,
                         direction: Direction, volume: int, offset: Offset) -> None:
        """发送平仓订单"""
        req: OrderRequest = OrderRequest(
            symbol=symbol,
            exchange=contract.exchange,
            direction=direction,
            type=OrderType.MARKET,
            volume=volume,
            price=0,
            offset=offset,
            reference=f"test_close_{offset.value}"
        )
        order_id: str = self.gateway.send_order(req)
        if order_id:
            self.gateway.write_log(f"平仓单已发送 [{direction.value} {offset.value} {volume}手]: {order_id}")
        else:
            self.gateway.write_log(f"平仓单发送失败 [{direction.value} {offset.value} {volume}手]")


