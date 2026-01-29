"""
跨期套利策略模块
包含白银跨期套利策略的实现
"""

from __future__ import annotations

import asyncio
import math
import sys
import threading
from datetime import datetime, timedelta, time
from time import sleep
from threading import Thread
from typing import Any, Union, Optional, TYPE_CHECKING, Dict

import numpy as np
import pandas as pd
from tqsdk.objs import Quote

from common.tqsdk_gateway import KLINES_WINDOWS
from common.vnpy_time import split_cross_day_time
from vnpy.trader.constant import Direction, Offset, Exchange, OrderType, Status
from vnpy.trader.object import OrderRequest, CancelRequest, ContractData, SubscribeRequest
from vnpy.event import Event

if TYPE_CHECKING:
    from common.gateway_tq import BaseGatewayTq

# 其他常量
MAX_FLOAT = sys.float_info.max
# 收盘前多少分钟停止交易
CLOSE_BEFORE_MINUTE = 15
# 订单更新时间，用于注册
EVENT_TQSDK_ORDER = "eTqsdkOrder"
# 开仓手数
DEFAULT_SLOT = 1
# 做一次差价开平的手续费成本,240，一跳15元
COMMISION_COST = 16
# 做一次差价就是4次下单，一次滑点设为3
SLIPPAGE_COST = 3 * 4
# 开仓时的盘口成本，以空差为例：  (近期买1价-远期卖1价) - (近期卖1价-远期买1价)   结果为负数就是成本，为正数就是利润
# 盘口成本为负时，说明价差还可能会上涨，比如会到-10，所以等价差从高点开始回落，再下单是比较好的时机
ORDERBOOK_COST = -4

def adjust_price(price: float) -> float:
    """将异常的浮点数最大值（MAX_FLOAT）数据调整为0"""
    if price == MAX_FLOAT:
        price = 0
    return price


class SpreadTradingStrategy:
    """
    跨期套利策略基类

    用于实现价差套利交易策略，包括开仓、平仓、止损等功能
    """

    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        """
        构造函数

        Parameters
        ----------
        gateway : BaseGatewayTq
            Gateway实例，用于访问交易功能和日志输出
        near_symbol : str
            近月合约代码
        far_symbol : str
            远月合约代码
        """
        self.gateway = gateway
        self.gateway_name = gateway.gateway_name
        self.near_symbol = near_symbol
        self.far_symbol = far_symbol

        # 策略参数（默认值，子类可以覆盖）
        self.upper_band = 105          # 上轨：做空差价开仓阈值
        self.middle_band = 35          # 中轨：平仓阈值
        self.lower_band = -30          # 下轨：做多差价开仓阈值
        self.transaction_volume = DEFAULT_SLOT    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.max_spread_cost = 25      # 最大买卖价差成本
        self.stop_loss_points = 50     # 止损点数
        self.max_price_limit_ratio = 0.8  # 涨跌停限制比例

        # 持仓状态字典
        # 结构：{"short_spread"|"long_spread": {...}}
        # short_spread: 做空价差持仓（卖近买远）
        # long_spread: 做多价差持仓（买近卖远）
        # 每个持仓包含以下字段：
        #   - open_time: 开仓时间
        #   - open_spread: 开仓时的价差
        #   - near_order_id: 近月合约订单ID
        #   - far_order_id: 远月合约订单ID
        #   - near_filled: 近月合约是否已成交（True=已成交，False=未成交）
        #   - far_filled: 远月合约是否已成交（True=已成交，False=未成交）
        self.spread_position: dict = {}

        # 待成交订单字典
        # 结构：{vt_order_id: {...}}
        # vt_order_id: 格式为 "gateway_name.orderid" 的订单唯一标识
        # 每个订单包含以下字段：
        #   - symbol: 合约代码（如"ag2604"）
        #   - direction: 方向（"LONG"=多头，"SHORT"=空头）
        #   - offset: 开平仓（"OPEN"=开仓，"CLOSE"=平仓，"CLOSETODAY"=平今，"CLOSEYESTERDAY"=平昨）
        #   - create_time: 订单创建时间戳
        #   - pair_order_id: 配对订单ID（另一条腿的订单ID）
        self.pending_orders: dict = {}

        # 订单状态映射字典
        # 结构：{vt_order_id: status}
        # vt_order_id: 格式为 "gateway_name.orderid" 的订单唯一标识
        # status: 订单状态（Status枚举：SUBMITTING, NOTTRADED, PARTTRADED, ALLTRADED, CANCELLED, REJECTED）
        self.order_status_map: dict = {}

        # 状态标志
        self.is_closing_time: bool = False  # 是否处于收盘前15分钟，为True时停止开新仓
        self.close_time_warned: bool = False  # 是否已经发出收盘警告日志，避免重复日志
        self.thread_active: bool = True  # 策略是否激活，False时停止收盘时间检查线程
        self.position_loaded: bool = False  # 是否已经加载过持仓信息，True后才开始交易

        # 启动收盘时间检查线程（每3分钟检查一次）
        # interrupt_event: 用于打断wait()等待，使线程能快速退出
        self.interrupt_event = threading.Event()
        self.closing_check_thread = Thread(target=self._check_closing_time_loop, daemon=True, name="closing_check_thread")
        self.closing_check_thread.start()

    def check_and_run(self, quotes_sub: Dict[str, Quote], klines_sub: Dict[str, pd.DataFrame]) -> None:
        """
        在await_update之后调用，检查是否可以执行策略

        逻辑：
        1. 检查持仓信息是否已加载，未加载则返回等待
        2. 检查近月和远月合约行情是否都已订阅
        3. 检查两个合约行情数据是否同步（时间戳差值<0.5秒）
        4. 以上条件都满足，调用on_tick执行策略逻辑

        Parameters
        ----------
        quotes_sub : Dict[str, Quote]
            已订阅的行情字典 {symbol: Quote}
        """
        # 如果还未加载持仓信息，先加载
        if not self.position_loaded:
            return  # 等待持仓信息加载完成


        self.klines_calculate(quotes_sub, klines_sub)



        if self.near_symbol in quotes_sub and self.far_symbol in quotes_sub:
            near_quote: Quote = quotes_sub[self.near_symbol]
            far_quote: Quote = quotes_sub[self.far_symbol]
            if self._check_data_sync(near_quote, far_quote):
                self.on_tick(near_quote, far_quote)

    def klines_calculate(self, quotes_sub: Dict[str, Quote], klines_sub: Dict[str, pd.DataFrame]):
        near_klines = klines_sub[self.near_symbol]
        far_klines = klines_sub[self.far_symbol]
        # 两个kline 都没变化就返回
        if (not self.gateway.tq_md_api.api.is_changing(near_klines)
                and not self.gateway.tq_md_api.api.is_changing(far_klines)):
            return
        # 确保有足够的数据
        if len(near_klines) < KLINES_WINDOWS or len(far_klines) < KLINES_WINDOWS:
            self.gateway.write_log(f"时间窗口不一致， near_klines: {len(near_klines)} far_klines: {len(far_klines)}")
            return

        # # 获取两个klines的最新时间，如果不一致，则缝合数据
        # near_kline_time = datetime.fromtimestamp(near_klines.datetime.iloc[-1] / 1e9)
        # far_kline_time = datetime.fromtimestamp(far_klines.datetime.iloc[-1] / 1e9)
        # if near_kline_time == far_kline_time:
        #
        #     # calulate_spread(near_klines, far_klines)
        #     # 获取窗口内价格
        #     near_close = near_klines.close.iloc[-KLINES_WINDOWS:]
        #     far_close = far_klines.close.iloc[-KLINES_WINDOWS:]
        #     # 计算价差
        #     spread = near_close - far_close
        #     # 计算均值和标准差
        #     mean = np.mean(spread)
        #     std = np.std(spread)
        #     # 计算上轨边界
        #     upper_bound = mean + max(K * std, THRESHOLD_DOWN)   # 出于风控，不能低于THRESHOLD_DOWN
        #     # 计算下轨边界
        #     lower_bound = mean - max(K * std, THRESHOLD_DOWN)
        #     # 存储前值
        #     pre_spread_value = (mean, std, upper_bound, lower_bound)
        #     # # logger.info(f"---near_klines时间：{near_kline_time}, far_klines时间：{far_kline_time}，合约时间相同")
        #
        # elif near_kline_time > far_kline_time:
        #     logger.info(f"---near_klines时间：{near_kline_time}, far_klines时间：{far_kline_time}， near_klines时间快")
        #     if pre_spread_value is not None:
        #         mean, std, upper_bound, lower_bound = pre_spread_value
        #     else:
        #         # 如果不交易，就写下日志
        #         print_klines(near_klines, far_klines)
        #         near_close = near_klines.close.iloc[-WINDOW-1:-1]
        #         far_close = far_klines.close.iloc[-WINDOW:]
        #         logger.info(near_close)
        #         logger.info(far_close)
        #         continue
        # else:
        #     logger.info(f"---near_klines时间：{near_kline_time}, far_klines时间：{far_kline_time}， far_klines时间快")
        #     if pre_spread_value is not None:
        #         mean, std, upper_bound, lower_bound = pre_spread_value
        #     else:
        #         # 如果不交易，就写下日志
        #         near_close = near_klines.close.iloc[-WINDOW:]
        #         far_close = far_klines.close.iloc[-WINDOW-1:-1]
        #         print_klines(near_klines, far_klines)
        #         logger.info(near_close)
        #         logger.info(far_close)
        #         continue

    def on_tick(self, near_quote: Quote, far_quote: Quote) -> None:
        """
        行情更新回调，在策略主循环中调用，执行完整的套利策略逻辑

        逻辑流程：
        1. 检查是否处于收盘时间，是则停止交易
        2. 计算三个价差：
           - real_short_spread: 可卖出价差（近月买价 - 远月卖价）
           - real_long_spread: 可买入价差（近月卖价 - 远月买价）
           - spread_cost: 买卖价差成本（滑点成本）
        3. 风控检查：
           - 买卖价差成本是否过大（>25点）
           - 是否接近涨跌停（80%限制）
           - 是否有待成交订单（有则等待成交或超时）
        4. 根据持仓状态执行相应操作：
           - 做空价差持仓：检查平仓或止损条件
           - 做多价差持仓：检查平仓或止损条件
           - 无持仓：检查开仓机会（价差是否突破上轨或下轨）

        Parameters
        ----------
        near_quote : Quote
            近月合约行情对象，包含价格、成交量、涨跌停等信息
        far_quote : Quote
            远月合约行情对象，包含价格、成交量、涨跌停等信息
        """

        # 如果处于收盘时间，不执行任何交易逻辑
        if self.is_closing_time:
            return

        try:
            # 计算价差
            # real_short_spread: 做空价差（可卖出价差）= 近月买价 - 远月卖价
            #   这是实际上能卖出的价差，用近月的买价减去远月的卖价
            real_short_spread = near_quote.bid_price1 - far_quote.ask_price1

            # real_long_spread: 做多价差（可买入价差）= 近月卖价 - 远月买价
            #   这是实际上能买入的价差，用近月的卖价减去远月的买价
            real_long_spread = near_quote.ask_price1 - far_quote.bid_price1

            # spread_cost: 买卖价差成本（滑点成本）
            #   这是同时买入近月、卖出远月的总成本，越大表示流动性越差
            spread_cost = (near_quote.ask_price1 - near_quote.bid_price1) + \
                         (far_quote.ask_price1 - far_quote.bid_price1)

            # 风控检查1：买卖价差成本检查
            # 如果买卖价差成本过大，说明市场流动性不足或波动剧烈，不宜交易
            if spread_cost > self.max_spread_cost:
                self.gateway.write_log(f"买卖价差成本过大: {spread_cost}，超过{self.max_spread_cost}，停止交易")
                return

            # 风控检查2：涨跌停检查
            # 检查价格是否接近涨跌停，是则停止交易
            if self._check_price_limit(near_quote, far_quote):
                return  # 接近涨跌停，停止交易

            # 风控检查3：待成交订单检查
            # 如果有待成交订单，检查是否超时，然后返回等待订单成交或超时
            if self.pending_orders:
                self._check_pending_orders_timeout()
                return  # 有待成交订单，等待成交或超时

            # 获取当前持仓类型（"short_spread"|"long_spread"|None）
            position_type = self._get_position_type()

            # 根据持仓状态执行相应操作
            if position_type == "short_spread":
                # 持有做空价差持仓，检查是否平仓或止损
                self._manage_short_spread_position(real_long_spread)
            elif position_type == "long_spread":
                # 持有多头价差持仓，检查是否平仓或止损
                self._manage_long_spread_position(real_short_spread)
            elif position_type is None and not self.is_closing_time:
                # 无持仓且不在收盘时间，检查开仓机会
                self._check_open_opportunity(real_short_spread, real_long_spread, near_quote, far_quote)
        except Exception as e:
            self.gateway.write_log(f"策略执行异常: {str(e)}")


    def _check_open_opportunity(self, real_short_spread: float, real_long_spread: float,
                                near_quote: Quote, far_quote: Quote) -> None:
        """
        检查开仓机会（在无持仓时调用）

        逻辑：
        1. 检查是否可以开仓（仓位控制，只允许1手持仓）
        2. 检查做空价差机会：real_short_spread > upper_band（105），价差过高时做空价差
        3. 检查做多价差机会：real_long_spread < lower_band（-30），价差过低时做多价差

        Parameters
        ----------
        real_short_spread : float
            可卖出价差（近月买价 - 远月卖价）
        real_long_spread : float
            可买入价差（近月卖价 - 远月买价）
        near_quote : Quote
            近月合约行情
        far_quote : Quote
            远月合约行情
        """
        # 先检查是否可以开仓（控制仓位，只允许1手持仓）
        if not self._check_can_open_position():
            return

        # 做空价差：价差过高（>105）
        # 卖出近月合约，买入远月合约，预期价差会回归到中轨（35）
        if real_short_spread > self.upper_band:
            self.open_short_spread(near_quote, far_quote, real_short_spread)
            self.gateway.write_log(f"做空价差开仓: {real_short_spread} > {self.upper_band}")
            return

        # 做多价差：价差过低（<-30）
        # 买入近月合约，卖出远月合约，预期价差会回归到中轨（35）
        if real_long_spread < self.lower_band:
            self.open_long_spread(near_quote, far_quote, real_long_spread)
            self.gateway.write_log(f"做多价差开仓: {real_long_spread} < {self.lower_band}")
            return

    def _manage_short_spread_position(self, current_spread: float) -> None:
        """
        管理做空价差持仓

        逻辑：
        1. 获取开仓时的价差
        2. 平仓条件：current_spread <= middle_band（35），价差回归到中轨
        3. 止损条件：current_spread > open_spread + 50，价差继续扩大超过50点

        Parameters
        ----------
        current_spread : float
            当前价差（real_long_spread，近月卖价 - 远月买价）
        """
        if "short_spread" not in self.spread_position:
            return

        # 获取开仓时的价差
        open_spread = self.spread_position["short_spread"]["open_spread"]

        # 平仓：价差回归到中轨（<=35）
        # 做空价差盈利了，平仓获利
        if current_spread <= self.middle_band:
            self.gateway.write_log(f"做空价差回归: {current_spread} <= {self.middle_band}，平仓")
            self.close_position()
            return

        # 止损：价差继续扩大（>开仓价+50）
        # 做空价差亏损了，止损平仓
        if current_spread > open_spread + self.stop_loss_points:
            self.gateway.write_log(f"做空价差止损: {current_spread} > {open_spread + self.stop_loss_points}，平仓")
            self.close_position()
            return

    def _manage_long_spread_position(self, current_spread: float) -> None:
        """
        管理做多价差持仓

        逻辑：
        1. 获取开仓时的价差
        2. 平仓条件：current_spread >= middle_band（35），价差回归到中轨
        3. 止损条件：current_spread < open_spread - 50，价差继续下跌超过50点

        Parameters
        ----------
        current_spread : float
            当前价差（real_short_spread，近月买价 - 远月卖价）
        """
        if "long_spread" not in self.spread_position:
            return

        # 获取开仓时的价差
        open_spread = self.spread_position["long_spread"]["open_spread"]

        # 平仓：价差回归到中轨（>=35）
        # 做多价差盈利了，平仓获利
        if current_spread >= self.middle_band:
            self.gateway.write_log(f"做多价差回归: {current_spread} >= {self.middle_band}，平仓")
            self.close_position()
            return

        # 止损：价差继续下跌（<开仓价-50）
        # 做多价差亏损了，止损平仓
        if current_spread < open_spread - self.stop_loss_points:
            self.gateway.write_log(f"做多价差止损: {current_spread} < {open_spread - self.stop_loss_points}，平仓")
            self.close_position()
            return

    def open_short_spread(self, near_quote: Quote, far_quote: Quote, spread: float) -> None:
        """
        开做空价差（卖近买远）

        逻辑：
        1. 获取近月和远月合约信息
        2. 下单1：卖出近月合约（SHORT, OPEN）
        3. 下单2：买入远月合约（LONG, OPEN）
        4. 如果两个订单都发送成功：
           - 记录到待成交订单字典（pending_orders）
           - 暂时记录到持仓字典（spread_position），但near_filled和far_filled都为False
        5. 如果有订单发送失败，撤销已发送的订单

        Parameters
        ----------
        near_quote : Quote
            近月合约行情
        far_quote : Quote
            远月合约行情
        spread : float
            开仓时的价差
        """
        try:
            # 获取合约信息
            near_contract = self.gateway.symbol_contract_map_tqsdk.get(self.near_symbol)
            far_contract = self.gateway.symbol_contract_map_tqsdk.get(self.far_symbol)

            if not near_contract or not far_contract:
                self.gateway.write_log("未找到合约信息")
                return

            # 下单1：卖出近月合约（SHORT, OPEN）
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=near_contract.exchange,
                direction=Direction.SHORT,
                type=OrderType.MARKET,
                volume=self.transaction_volume,
                price=0,
                offset=Offset.OPEN,
                reference=f"spread_short_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 下单2：买入远月合约（LONG, OPEN）
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=far_contract.exchange,
                direction=Direction.LONG,
                type=OrderType.MARKET,
                volume=self.transaction_volume,
                price=0,
                offset=Offset.OPEN,
                reference=f"spread_short_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单（用于超时检查和状态跟踪）
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": "SHORT",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_far  # 配对订单ID
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": "LONG",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_near  # 配对订单ID
                }

                # 暂时记录持仓（实际要等订单成交，所以near_filled和far_filled都为False）
                # 这样在订单状态更新时可以更新这些标志
                self.spread_position["short_spread"] = {
                    "open_spread": spread,        # 开仓时的价差
                    "open_time": datetime.now(),  # 开仓时间
                    "near_order_id": order_id_near,  # 近月订单ID
                    "far_order_id": order_id_far,    # 远月订单ID
                    "near_filled": False,  # 近月是否成交（初始为False）
                    "far_filled": False    # 远月是否成交（初始为False）
                }

                self.gateway.write_log(f"做空价差开仓订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("做空价差开仓失败")
                # 清理部分订单（如果有一个订单发送成功，另一个失败）
                if order_id_near:
                    self._cancel_order_only(order_id_near)
                if order_id_far:
                    self._cancel_order_only(order_id_far)
                self.spread_position.clear()

        except Exception as e:
            self.gateway.write_log(f"做空价差开仓异常: {str(e)}")

    def open_long_spread(self, near_quote: Quote, far_quote: Quote, spread: float) -> None:
        """
        开做多价差（买近卖远）

        逻辑：
        1. 获取近月和远月合约信息
        2. 下单1：买入近月合约（LONG, OPEN）
        3. 下单2：卖出远月合约（SHORT, OPEN）
        4. 如果两个订单都发送成功：
           - 记录到待成交订单字典（pending_orders）
           - 暂时记录到持仓字典（spread_position），但near_filled和far_filled都为False
        5. 如果有订单发送失败，撤销已发送的订单

        Parameters
        ----------
        near_quote : Quote
            近月合约行情
        far_quote : Quote
            远月合约行情
        spread : float
            开仓时的价差
        """
        try:
            # 获取合约信息
            near_contract = self.gateway.symbol_contract_map_tqsdk.get(self.near_symbol)
            far_contract = self.gateway.symbol_contract_map_tqsdk.get(self.far_symbol)

            if not near_contract or not far_contract:
                self.gateway.write_log("未找到合约信息")
                return

            # 下单1：买入近月合约（LONG, OPEN）
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=near_contract.exchange,
                direction=Direction.LONG,
                type=OrderType.MARKET,
                volume=self.transaction_volume,
                price=0,
                offset=Offset.OPEN,
                reference=f"spread_long_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 下单2：卖出远月合约（SHORT, OPEN）
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=far_contract.exchange,
                direction=Direction.SHORT,
                type=OrderType.MARKET,
                volume=self.transaction_volume,
                price=0,
                offset=Offset.OPEN,
                reference=f"spread_long_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单（用于超时检查和状态跟踪）
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": "LONG",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_far  # 配对订单ID
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": "SHORT",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_near  # 配对订单ID
                }

                # 暂时记录持仓（实际要等订单成交，所以near_filled和far_filled都为False）
                self.spread_position["long_spread"] = {
                    "open_spread": spread,        # 开仓时的价差
                    "open_time": datetime.now(),  # 开仓时间
                    "near_order_id": order_id_near,  # 近月订单ID
                    "far_order_id": order_id_far,    # 远月订单ID
                    "near_filled": False,  # 近月是否成交（初始为False）
                    "far_filled": False    # 远月是否成交（初始为False）
                }

                self.gateway.write_log(f"做多价差开仓订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("做多价差开仓失败")
                # 清理部分订单（如果有一个订单发送成功，另一个失败）
                if order_id_near:
                    self._cancel_order_only(order_id_near)
                if order_id_far:
                    self._cancel_order_only(order_id_far)
                self.spread_position.clear()

        except Exception as e:
            self.gateway.write_log(f"做多价差开仓异常: {str(e)}")

    def close_position(self, force: bool = False) -> None:
        """
        平仓价差持仓

        逻辑：
        1. 获取当前持仓类型（"short_spread"|"long_spread"）
        2. 根据持仓类型确定平仓方向：
           - 做空价差持仓：买入近月，卖出远月（LONG, SHORT）
           - 做多价差持仓：卖出近月，买入远月（SHORT, LONG）
        3. 调用_send_close_orders发送平仓订单
        4. 清空持仓字典（spread_position）

        Parameters
        ----------
        force : bool
            是否强制平仓（目前未使用此参数，保留用于未来扩展）
        """
        try:
            # 获取当前持仓类型
            position_type = self._get_position_type()
            if not position_type:
                return  # 无持仓
            if force:
                # 获取开仓时的手数
                near_volume = self.spread_position["short_spread"]["near_volume"]
                far_volume = self.spread_position["long_spread"]["far_volume"]
            else:
                near_volume = DEFAULT_SLOT
                far_volume = DEFAULT_SLOT

            if position_type == "short_spread":
                # 平做空价差：买入近月，卖出远月
                self._send_close_orders(Direction.LONG, Direction.SHORT,
                                        near_volume=near_volume, far_volume=far_volume)
            elif position_type == "long_spread":
                # 平做多价差：卖出近月，买入远月
                self._send_close_orders(Direction.SHORT, Direction.LONG,
                                        near_volume=near_volume, far_volume=far_volume)

            # 清空持仓字典
            self.spread_position.clear()
            self.gateway.write_log("价差持仓已平仓")

        except Exception as e:
            self.gateway.write_log(f"平仓异常: {str(e)}")

    def _send_close_orders(self, near_direction: Direction, far_direction: Direction,
                           near_volume=DEFAULT_SLOT, far_volume=DEFAULT_SLOT) -> None:
        """
        发送平仓订单（同时平两条腿）

        逻辑：
        1. 获取近月和远月合约信息
        2. 下单1：平近月合约（使用CLOSETODAY，因为策略只做当日交易）
        3. 下单2：平远月合约（使用CLOSETODAY）
        4. 如果两个订单都发送成功，记录到待成交订单字典

        Parameters
        ----------
        near_direction : Direction
            近月合约平仓方向（LONG=平空，SHORT=平多）
        far_direction : Direction
            远月合约平仓方向（LONG=平空，SHORT=平多）
        """
        try:
            near_contract = self.gateway.symbol_contract_map_tqsdk.get(self.near_symbol)
            far_contract = self.gateway.symbol_contract_map_tqsdk.get(self.far_symbol)

            if not near_contract or not far_contract:
                self.gateway.write_log("未找到合约信息")
                return

            # 下单1：平近月合约
            # 使用CLOSETODAY因为策略只做当日交易，不会有昨仓
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=near_contract.exchange,
                direction=near_direction,
                type=OrderType.MARKET,
                volume=near_volume,
                price=0,
                offset=Offset.CLOSETODAY,  # 平今仓
                reference=f"spread_close_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 下单2：平远月合约
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=far_contract.exchange,
                direction=far_direction,
                type=OrderType.MARKET,
                volume=far_volume,
                price=0,
                offset=Offset.CLOSETODAY,  # 平今仓
                reference=f"spread_close_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单（用于超时检查）
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": near_direction.value,
                    "offset": "CLOSETODAY",
                    "create_time": create_time,
                    "pair_order_id": order_id_far  # 配对订单ID
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": far_direction.value,
                    "offset": "CLOSETODAY",
                    "create_time": create_time,
                    "pair_order_id": order_id_near  # 配对订单ID
                }

                self.gateway.write_log(f"平仓订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("平仓订单发送失败")

        except Exception as e:
            self.gateway.write_log(f"发送平仓订单异常: {str(e)}")

    def on_order_status_update(self, vt_orderid: str, status: Status) -> None:
        """
        订单状态更新回调（从TtsTdApi的onRtnOrder调用）

        逻辑：
        1. 更新订单状态映射（order_status_map）
        2. 检查是否是待成交订单，不是则返回
        3. 根据订单状态执行相应操作：
           - ALLTRADED（全部成交）：
             * 更新spread_position中的near_filled或far_filled标志
             * 从pending_orders中移除该订单
             * 检查配对订单是否也成交，如果都成交则开仓完成
           - REJECTED/CANCELLED（被拒绝/撤销）：
             * 从pending_orders中移除该订单
             * 检查是否单腿成交，是则立即平仓

        Parameters
        ----------
        vt_orderid : str
            订单ID（格式："gateway_name.orderid"）
        status : Status
            订单状态（SUBMITTING, NOTTRADED, PARTTRADED, ALLTRADED, CANCELLED, REJECTED）
        """
        try:
            # 更新订单状态映射
            self.order_status_map[vt_orderid] = status

            # 检查是否是待成交订单
            if vt_orderid not in self.pending_orders:
                return

            order_info = self.pending_orders[vt_orderid]
            pair_order_id = order_info.get("pair_order_id")

            # 情况1：订单完全成交
            if status == Status.ALLTRADED:
                self.gateway.write_log(f"订单完全成交: {vt_orderid}")

                # 更新spread_position中的成交标志
                position_type = self._get_position_type()
                if position_type and position_type in self.spread_position:
                    position = self.spread_position[position_type]
                    if position.get("near_order_id") == vt_orderid:
                        # 近月订单成交，设置near_filled为True
                        position["near_filled"] = True
                    elif position.get("far_order_id") == vt_orderid:
                        # 远月订单成交，设置far_filled为True
                        position["far_filled"] = True

                # 从待成交订单中移除
                del self.pending_orders[vt_orderid]

                # 检查配对订单是否也成交
                if pair_order_id and pair_order_id not in self.pending_orders:
                    # 两条腿都成交，开仓完成
                    self.gateway.write_log(f"价差订单对成交完成")

            # 情况2：订单被拒绝或撤销
            elif status in [Status.REJECTED, Status.CANCELLED]:
                self.gateway.write_log(f"订单{status.value}: {vt_orderid}")

                # 从待成交订单中移除
                del self.pending_orders[vt_orderid]

                # 处理单腿成交（如果另一条腿已经成交，需要立即平仓）
                self._handle_partial_fill()

        except Exception as e:
            self.gateway.write_log(f"订单状态更新异常: {str(e)}")

    def init_Event(self) -> None:
        """初始化查询任务"""
        # self.query_functions: list = [self.query_account, self.query_position]
        self.gateway.event_engine.register(EVENT_TQSDK_ORDER, self.on_order_exception)

    def on_order_exception(self, event: Event) -> None:
        error_event_data: dict = event.data
        data: dict = error_event_data[0]
        error: dict = error_event_data[1]
        reqid: int = error_event_data[2]
        self.gateway.write_log(f"订单错误：{data} 错误信息：{error}  reqid:{reqid}")
        # if error and error["ErrorID"]==1011:
        #     self.pending_orders

        pass
    def stop(self) -> None:
        """停止策略"""
        self.thread_active = False
        # 打断interrupt_event.wait()等待
        self.interrupt_event.set()
        # 等待收盘检查线程结束
        if hasattr(self, 'closing_check_thread') and self.closing_check_thread:
            self.closing_check_thread.join(timeout=3)

    def on_position_update(self, positions: list) -> None:
        """持仓更新回调（从TdApi的onRspQryInvestorPosition调用）

        Parameters
        ----------
        positions : list
            持仓列表
        """
        # 如果已经加载过持仓，就不需要再处理了
        # if self.position_loaded:
        #     return

        try:
            if not self.position_loaded:
                self.gateway.write_log("收到持仓更新回调，开始处理...")

            # 检查是否有ag2604和ag2606的持仓
            near_long_positions = []  # 近期多头
            near_short_positions = []  # 近期空头
            far_long_positions = []    # 远期多头
            far_short_positions = []   # 远期空头

            for pos in positions:
                if pos.volume > 0 and pos.symbol in [self.near_symbol, self.far_symbol]:
                    # self.gateway.write_log(f"发现{pos.symbol}持仓: {pos.direction.value} {pos.volume}手")

                    # 分类记录
                    if pos.symbol == self.near_symbol:
                        if pos.direction == Direction.LONG:
                            near_long_positions.append(pos)
                        else:
                            near_short_positions.append(pos)
                    else:  # far_symbol
                        if pos.direction == Direction.LONG:
                            far_long_positions.append(pos)
                        else:
                            far_short_positions.append(pos)

            # 根据持仓情况恢复策略状态
            position_restored = False

            # 检查是否是做空价差持仓（near空 + far多）
            if (near_short_positions and far_long_positions and
                len(near_short_positions) == self.transaction_volume and
                len(far_long_positions) == self.transaction_volume):
                if not self.position_loaded:
                    self.gateway.write_log("检测到做空价差持仓，已恢复策略状态")
                self.spread_position["short_spread"] = {
                    "open_time": datetime.now(),  # 使用当前时间作为开仓时间
                    "open_spread": near_short_positions[0].price - far_long_positions[0].price,  # 原始开仓价是近期买1-远期卖1 或者近期卖1-远期买1，这里只能用两个实际成交价相减
                    "near_order_id": "",
                    "far_order_id": "",
                    "near_filled": True,
                    "near_volume": near_short_positions[0].volume,
                    "far_filled": True,
                    "far_volume": far_long_positions[0].volume,
                }
                position_restored = True

            # 检查是否是做多价差持仓（near多 + far空）
            elif (near_long_positions and far_short_positions and
                  len(near_long_positions) == self.transaction_volume and
                  len(far_short_positions) == self.transaction_volume):
                if not self.position_loaded:
                    self.gateway.write_log("检测到做多价差持仓，已恢复策略状态")
                self.spread_position["long_spread"] = {
                    "open_time": datetime.now(),  # 使用当前时间作为开仓时间
                    "open_spread": near_long_positions[0].price - far_short_positions[0].price,  # 无法获取原始开仓价差，设为0
                    "near_order_id": "",
                    "far_order_id": "",
                    "near_filled": True,
                    "near_volume": near_long_positions[0].volume,
                    "far_filled": True,
                    "far_volume": far_short_positions[0].volume,
                }
                position_restored = True

            # 保证系统启动时只执行一次
            if not self.position_loaded:
                if position_restored:
                    self.gateway.write_log("持仓状态恢复完成，策略可以继续交易")
                else:
                    self.gateway.write_log("未检测到价差持仓，策略准备就绪")
            # 这个状态设置后，才可以交易
            self.position_loaded = True
            self.position_loaded = True

        except Exception as e:
            self.gateway.write_log(f"处理持仓更新异常: {str(e)}")
            # self.position_loaded = True  # 即使失败也设置为已加载，避免重复处理
            self.position_loaded = False  # 持仓更新异常不允许交易

    def _check_can_open_position(self) -> bool:
        """
        检查是否可以开新仓

        Returns
        -------
        bool
            True表示可以开仓，False表示已有持仓不允许开仓
        """
        # 如果已有持仓，不允许开新仓（因为transaction_volume=1，只允许1手持仓）
        if self.spread_position:
            self.gateway.write_log(f"已有持仓: {list(self.spread_position.keys())}，不允许开新仓")
            return False

        return True

    def _check_pending_orders_timeout(self) -> None:
        """检查待成交订单超时"""
        try:
            current_time = datetime.now().timestamp()
            timeout_orders = []

            for order_id, order_info in self.pending_orders.items():
                if current_time - order_info["create_time"] > self.order_timeout:
                    timeout_orders.append(order_id)

            if timeout_orders:
                self.gateway.write_log(f"订单超时: {timeout_orders}，撤销订单")
                for order_id in timeout_orders:
                    self._cancel_order(order_id)

                # 检查是否有单腿成交
                self._handle_partial_fill()

        except Exception as e:
            self.gateway.write_log(f"检查订单超时异常: {str(e)}")

    def _cancel_order(self, vt_orderid: str) -> None:
        """
        撤销订单

        Parameters
        ----------
        vt_orderid : str
            订单ID
        """
        try:
            # 从pending_orders中获取订单信息
            if vt_orderid in self.pending_orders:
                symbol = self.pending_orders[vt_orderid]["symbol"]
                contract = self.gateway.symbol_contract_map_tqsdk.get(symbol)
                if contract:
                    # vt_orderid格式: GATEWAYNAME.orderid
                    parts = vt_orderid.split('.')
                    orderid = parts[-1] if len(parts) > 1 else vt_orderid

                    req = CancelRequest(
                        orderid=orderid,
                        symbol=symbol,
                        exchange=contract.exchange
                    )
                    self.gateway.cancel_order(req)
                    self.gateway.write_log(f"撤销订单: {vt_orderid}")

                # 从待成交订单中移除
                del self.pending_orders[vt_orderid]

        except Exception as e:
            self.gateway.write_log(f"撤销订单异常: {str(e)}")

    def _cancel_order_only(self, vt_orderid: str) -> None:
        """
        仅撤销订单，不处理待成交订单列表

        Parameters
        ----------
        vt_orderid : str
            订单ID
        """
        try:
            symbol = None
            # 尝试从pending_orders中获取
            if vt_orderid in self.pending_orders:
                symbol = self.pending_orders[vt_orderid]["symbol"]

            # 如果找不到，从订单ID中提取symbol
            if not symbol:
                # 这里可以添加逻辑从订单ID解析symbol
                return

            contract = self.gateway.symbol_contract_map_tqsdk.get(symbol)
            if contract:
                parts = vt_orderid.split('.')
                orderid = parts[-1] if len(parts) > 1 else vt_orderid

                req = CancelRequest(
                    orderid=orderid,
                    symbol=symbol,
                    exchange=contract.exchange
                )
                self.gateway.cancel_order(req)

        except Exception as e:
            self.gateway.write_log(f"撤销订单异常: {str(e)}")

    def _handle_partial_fill(self) -> None:
        """处理单腿成交情况"""
        try:
            position_type = self._get_position_type()
            if not position_type:
                return

            position = self.spread_position[position_type]

            # 检查是否单腿成交
            if position.get("near_filled") and not position.get("far_filled"):
                # near成交，far未成交，立即平仓near
                self.gateway.write_log("单腿成交：near已成交，far未成交，立即平仓near")
                self._emergency_close_position(position_type, "near")
            elif position.get("far_filled") and not position.get("near_filled"):
                # far成交，near未成交，立即平仓far
                self.gateway.write_log("单腿成交：far已成交，near未成交，立即平仓far")
                self._emergency_close_position(position_type, "far")

        except Exception as e:
            self.gateway.write_log(f"处理单腿成交异常: {str(e)}")

    def _emergency_close_position(self, position_type: str, leg: str) -> None:
        """
        紧急平仓单腿

        Parameters
        ----------
        position_type : str
            持仓类型（"short_spread" 或 "long_spread"）
        leg : str
            要平仓的腿（"near" 或 "far"）
        """
        try:
            if position_type == "short_spread":
                if leg == "near":
                    # 平near空头
                    self._send_emergency_close_order(self.near_symbol, Direction.LONG)
                else:
                    # 平far多头
                    self._send_emergency_close_order(self.far_symbol, Direction.SHORT)
            elif position_type == "long_spread":
                if leg == "near":
                    # 平near多头
                    self._send_emergency_close_order(self.near_symbol, Direction.SHORT)
                else:
                    # 平far空头
                    self._send_emergency_close_order(self.far_symbol, Direction.LONG)

            # 清空持仓
            self.spread_position.clear()

        except Exception as e:
            self.gateway.write_log(f"紧急平仓异常: {str(e)}")

    def _send_emergency_close_order(self, symbol: str, direction: Direction) -> None:
        """
        发送紧急平仓订单

        Parameters
        ----------
        symbol : str
            合约代码
        direction : Direction
            方向
        """
        try:
            contract = self.gateway.symbol_contract_map_tqsdk.get(symbol)
            if not contract:
                return

            req = OrderRequest(
                symbol=symbol,
                exchange=contract.exchange,
                direction=direction,
                type=OrderType.MARKET,
                volume=self.transaction_volume,
                price=0,
                offset=Offset.CLOSETODAY,
                reference=f"emergency_close_{symbol}"
            )
            order_id = self.gateway.send_order(req)
            self.gateway.write_log(f"紧急平仓订单已发送: {symbol} {direction.value} {order_id}")

        except Exception as e:
            self.gateway.write_log(f"发送紧急平仓订单异常: {str(e)}")

    def _check_data_sync(self, near_quote: Quote, far_quote: Quote) -> bool:
        """
        检查数据同步

        Parameters
        ----------
        near_quote : Quote
            近月合约行情
        far_quote : Quote
            远月合约行情

        Returns
        -------
        bool
            是否数据同步
        """
        try:
            if near_quote.datetime=='' or far_quote.datetime=='':
                return False
            # quote.datetime格式：2025-12-17 22:28:03.500001
            # 去除datetime秒后面的5个0（字符串切片：去掉最后5个字符） 结果："13:30:00.5"
            near_time_processed = str(near_quote.datetime)[:-5]
            far_time_processed = str(far_quote.datetime)[:-5]

            near_time = datetime.strptime(near_time_processed, '%Y-%m-%d %H:%M:%S.%f')
            far_time = datetime.strptime(far_time_processed, '%Y-%m-%d %H:%M:%S.%f')

            # 关键：带精度容错判断是否为0.5秒（避免浮点数精度问题）
            if abs(near_time - far_time) > timedelta(milliseconds=500):
                # 要求时间戳同步（允许0.5秒误差）
                return False

            # 要求两个合约都有价格数据
            if math.isnan(near_quote.last_price) or math.isnan(far_quote.last_price):
                return False
        except Exception as e:
            self.gateway.write_log(f"检查数据同步异常: {str(e)}")
            return False

        return True

    def _check_closing_time_loop(self) -> None:
        """收盘时间检查循环（在独立线程中运行，每分钟检查一次）"""
        while self.thread_active:
            try:
                # sleep(60)  # 每分钟检查一次
                self.interrupt_event.wait(180)

                # 获取near合约的行情
                if self.near_symbol not in self.gateway.tq_md_api.quotes:
                    continue

                near_quote = self.gateway.tq_md_api.quotes[self.near_symbol]
                if not near_quote or near_quote.datetime=='':
                    continue

                # 检查是否进入收盘时间
                if self._is_closing_time(near_quote):
                    if not self.is_closing_time:
                        # 为了提升性能，这里不加锁了，所以执行两次，避免同步问题
                        self.is_closing_time = True
                        self.is_closing_time = True
                        if not self.close_time_warned:
                            self.gateway.write_log("进入收盘前5分钟，停止新开仓")
                            self.close_time_warned = True

                    # 强制平仓
                    if self.spread_position:
                        self.gateway.write_log("收盘前强制平仓")
                        self.close_position(force=True)
                else:
                    # 已经不在收盘时间，重置标志
                    if self.is_closing_time:
                        self.gateway.write_log("已过收盘时间，恢复交易")
                        # 为了提升性能，这里不加锁了，所以执行两次，避免同步问题
                        self.is_closing_time = False
                        self.is_closing_time = False
                        self.close_time_warned = False

            except Exception as e:
                if self.thread_active:
                    self.gateway.write_log(f"收盘时间检查异常: {str(e)}")
                # break
        print("收盘时间检查关闭")

    def _is_closing_time(self, quote: Quote) -> bool:
        """
        判断是否处于收盘前5分钟

        Parameters
        ----------
        quote : Quote
            行情数据

        Returns
        -------
        bool
            是否处于收盘前5分钟
        """
        # 交易时段格式：'trading_time': {"day": [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]], "night": [["21:00:00", "26:30:00"]]}
        trading_time = quote.trading_time
        current_time = datetime.now().time()

        # 遍历所有交易时段（day和night）
        for period_type in ["day", "night"]:
            if period_type not in trading_time:
                continue

            periods = trading_time[period_type]
            second_end_hour = int(periods[0][1].split(':')[0])
            if second_end_hour > 23:
                # 如果隔夜就拆成两队
                periods = split_cross_day_time(periods)

            for period in periods:
                start_str = period[0]  # "09:00:00"
                end_str = period[1]    # "10:15:00"

                if end_str == '23:59:59' and len(periods)==2:
                    # 夜盘的隔夜逻辑，在夜盘第一段，可以跳过'23:59:59'
                    continue

                start_time = time.fromisoformat(start_str)
                end_time = time.fromisoformat(end_str)

                # 判断是否在收盘前5分钟
                closing_time = (datetime.combine(datetime.today(), end_time) -
                              timedelta(minutes=CLOSE_BEFORE_MINUTE)).time()

                # 如果在收盘前5分钟到收盘时间之间
                if closing_time <= current_time <= end_time:
                    return True

        return False

    def _check_price_limit(self, near_quote: Quote, far_quote: Quote) -> bool:
        """
        检查是否接近涨跌停

        Parameters
        ----------
        near_quote : Quote
            近月合约行情
        far_quote : Quote
            远月合约行情

        Returns
        -------
        bool
            是否接近涨跌停（True表示停止交易）
        """
        try:
            # 计算near合约的涨跌停幅度
            near_upper_move = (near_quote.upper_limit - near_quote.pre_settlement) * self.max_price_limit_ratio
            near_lower_move = (near_quote.pre_settlement - near_quote.lower_limit) * self.max_price_limit_ratio

            # 计算far合约的涨跌停幅度
            far_upper_move = (far_quote.upper_limit - far_quote.pre_settlement) * self.max_price_limit_ratio
            far_lower_move = (far_quote.pre_settlement - far_quote.lower_limit) * self.max_price_limit_ratio

            # 检查当前价格变动
            near_change = near_quote.last_price - near_quote.pre_settlement
            far_change = far_quote.last_price - far_quote.pre_settlement

            # 如果接近涨跌停，返回True
            if near_change >= near_upper_move or near_change <= -near_lower_move:
                self.gateway.write_log(f"{self.near_symbol}接近涨跌停，停止交易")
                return True
            if far_change >= far_upper_move or far_change <= -far_lower_move:
                self.gateway.write_log(f"{self.far_symbol}接近涨跌停，停止交易")
                return True

            return False
        except Exception as e:
            self.gateway.write_log(f"检查涨跌停异常: {str(e)}")
            return False

    def _get_position_type(self) -> Optional[str]:
        """
        获取当前持仓类型

        Returns
        -------
        Optional[str]
            "short_spread" 或 "long_spread" 或 None
        """
        if "short_spread" in self.spread_position:
            return "short_spread"
        elif "long_spread" in self.spread_position:
            return "long_spread"
        else:
            return None


class AgSpreadStrategy(SpreadTradingStrategy):
    """
    白银跨期套利策略

    交易ag2604（近月）和ag2606（远月）的价差回归
    """

    def __init__(self, gateway: "BaseGatewayTq"):
        """
        构造函数

        Parameters
        ----------
        gateway : BaseGatewayTq
            Gateway实例
        """
        super().__init__(gateway, "ag2604", "ag2606")

        # 设置白银特定的策略参数
        self.upper_band = 105           # 上轨：做空差价开仓阈值
        self.middle_band = 35           # 中轨：平仓阈值
        self.lower_band = -30           # 下轨：做多差价开仓阈值

        self.subscribe_ag()             # 订阅跨期套利合约

    def subscribe_ag(self):

        # 此处不用判断合约是否存在，因为此时Gateway还未连接，
        # 只是把订阅的合约预存在gateway的subscribed对象里
        # contract_near: ContractData | None = self.gateway.symbol_contract_map_tqsdk.get(self.near_symbol, None)
        # if not contract_near:
        #     return
        # contract_far: ContractData | None = self.gateway.symbol_contract_map_tqsdk.get(self.far_symbol, None)
        # if not contract_near:
        #     return

        req_near: SubscribeRequest = SubscribeRequest(
            symbol=self.near_symbol, exchange=Exchange.SHFE
        )
        req_far: SubscribeRequest = SubscribeRequest(
            symbol=self.far_symbol, exchange=Exchange.SHFE
        )

        self.gateway.subscribe(req_near)
        self.gateway.subscribe(req_far)
        self.gateway.write_log(f"订阅跨期合约{self.near_symbol}和{self.far_symbol}")

