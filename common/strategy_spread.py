"""
跨期套利策略模块
包含白银跨期套利策略的实现
"""

from __future__ import annotations

import math
import sys
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta, time
from typing import TYPE_CHECKING, Dict, Literal

import numpy as np
import pandas as pd
from tqsdk.objs import Quote

from vnpy.trader.constant import Direction, Offset, Exchange, OrderType, Status
from vnpy.trader.object import OrderRequest, CancelRequest, SubscribeRequest, PositionData
from vnpy.event import Event

if TYPE_CHECKING:
    from common.gateway_tq import BaseGatewayTq

# 其他常量
MAX_FLOAT = sys.float_info.max
# 订单更新事件，用于注册
EVENT_ERROR_ORDER = "eTqsdkOrder"
# Quote更新事件，用于注册
EVENT_UPDATE_QUOTE = "eTqsdkUpdateQuote"
# 空差持仓
SHORT_SPREAD_TYPE = "short_spread"
# 多差持仓
LONG_SPREAD_TYPE = "long_spread"

def adjust_price(price: float) -> float:
    """将异常的浮点数最大值（MAX_FLOAT）数据调整为0"""
    if price == MAX_FLOAT:
        price = 0
    return price

def get_market_price(near_quote: Quote, far_quote: Quote, exchange: Exchange, position_type) -> tuple[
    Literal[OrderType.LIMIT, OrderType.MARKET], float, float]:
    # 两家上海的交易所不支持市价指令，使用涨跌停价回撤10%作为市价

    if exchange in [Exchange.SHFE, Exchange.INE]:
        order_type = OrderType.LIMIT
        if position_type == SHORT_SPREAD_TYPE:
            near_price = near_quote.lower_limit + (near_quote.pre_settlement - near_quote.lower_limit) * 0.1
            far_price = far_quote.upper_limit - (far_quote.upper_limit - far_quote.pre_settlement) * 0.1
        else:
            near_price = near_quote.upper_limit -(near_quote.upper_limit - near_quote.pre_settlement) * 0.1
            far_price = far_quote.lower_limit + (far_quote.pre_settlement - far_quote.lower_limit) * 0.1
    else:
        order_type = OrderType.MARKET
        near_price = 0.0
        far_price = 0.0
    return order_type, far_price, near_price

class BaseStrategy(ABC):

    def __init__(self, gateway: "BaseGatewayTq"):
        self.gateway = gateway

        # 是否可交易
        self.is_tradable = False

        self.max_price_limit_ratio = 0.8  # 涨跌停限制比例

        # 持仓状态字典（跨期套利特有）
        # 结构：{"spread_position": {...}}
        # 只会有一个价差持仓，通过 position_type 字段标记类型
        # 每个持仓包含以下字段：
        #   - position_type: 持仓类型（"short_spread"|"long_spread"）
        #   - open_time: 开仓时间
        #   - open_spread: 开仓时的价差
        #   - near_order_id: 近月合约订单ID
        #   - far_order_id: 远月合约订单ID
        #   - near_filled: 近月合约是否已成交（True=已成交，False=未成交）
        #   - far_filled: 远月合约是否已成交（True=已成交，False=未成交）
        #   - near_yd_volume: 近月合约昨仓数量（用于判断平今/平昨）
        #   - far_yd_volume: 远月合约昨仓数量（用于判断平今/平昨）
        self.global_position: dict[str, dict] = defaultdict(dict)

        # 待成交订单字典
        # 结构：{vt_order_id: {...}}
        # vt_order_id: 格式为 "gateway_name.orderid" 的订单唯一标识
        # 每个订单包含以下字段：
        #   - symbol: 合约代码（如"ag2604"）
        #   - direction: 方向（"LONG"=多头，"SHORT"=空头）
        #   - offset: 开平仓（"OPEN"=开仓，"CLOSE"=平仓，"CLOSETODAY"=平今，"CLOSEYESTERDAY"=平昨）
        #   - create_time: 订单创建时间戳
        #   - pair_order_id: 配对订单ID（另一条腿的订单ID，可选）
        self.pending_orders: dict = {}

        # 订单状态映射字典
        # 结构：{vt_order_id: status}
        # vt_order_id: 格式为 "gateway_name.orderid" 的订单唯一标识
        # status: 订单状态（Status枚举：SUBMITTING, NOTTRADED, PARTTRADED, ALLTRADED, CANCELLED, REJECTED）
        self.order_status_map: dict = {}

        # 0是近期，1是远期
        self.spread_quotes: list[Quote] = []
        self.spread_klines: list[pd.DataFrame] = []

    def start(self):
        """启动策略"""
        # 停止风险管理线程
        if hasattr(self, 'risk_manager'):
            self.risk_manager.start()

    def stop(self) -> None:
        """停止策略"""
        # 停止风险管理线程
        if hasattr(self, 'risk_manager'):
            self.risk_manager.stop()

    @abstractmethod
    def close_position(self, force: bool = False) -> None:
        # 平仓函数
        pass

    @abstractmethod
    def get_symbols(self) -> list[str]:
        # 平仓函数
        pass


class SpreadTradingStrategy(BaseStrategy):
    """
    跨期套利策略基类

    用于实现价差套利交易策略，包括开仓、平仓、止损等功能
    """

    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str, exchange: Exchange) -> None:
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
        super().__init__(gateway)

        self.gateway_name = gateway.gateway_name
        self.near_symbol = near_symbol
        self.far_symbol = far_symbol
        self.exchange = exchange

        # 策略参数（默认值，子类可以覆盖）
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.min_profit_points = 10    # 基本利润（最小盈利）
        self.slippage_points = 3 * 4   # 做一次差价就是4次下单，一次滑点设为3
        self.commission_point = 16     # 做一次差价开平的手续费成本,240，一跳15元
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 1 * 60  # kline的请求周期

        # 计算成本用
        self.current_spread_indicator = None
        self.static_spread_cost = self.commission_point + self.slippage_points + self.min_profit_points

        # 状态标志（跨期套利特有）
        self.position_loaded: bool = False  # 是否已经加载过持仓信息，True后才开始交易
        self.approaching_daily_limit = False  # 接近当日的涨停价或跌停价

        # 注册订单异常等事件
        self.init_Event()

        self.subscribe_spread()

    def subscribe_spread(self):

        req_near: SubscribeRequest = SubscribeRequest(
            symbol=self.near_symbol, exchange=self.exchange,
        )
        req_far: SubscribeRequest = SubscribeRequest(
            symbol=self.far_symbol, exchange=self.exchange,
        )

        self.gateway.subscribe(req_near)
        self.gateway.subscribe(req_far)
        self.gateway.write_log(f"订阅跨期合约{self.near_symbol}和{self.far_symbol}")

    def get_symbols(self) -> list[str]:
        return [self.near_symbol, self.far_symbol]

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
        klines_sub : Dict[str, pd.DataFrame]
            已订阅的行情字典 {symbol: Quote}
        """
        # 检查持仓信息是否已加载，未加载则返回等待
        if not self.position_loaded:
            return  # 等待持仓信息加载完成

        # 检查近月和远月合约行情是否都已订阅
        if self.near_symbol in quotes_sub and self.far_symbol in quotes_sub:
            # 获取quote信息
            near_quote: Quote = quotes_sub[self.near_symbol]
            far_quote: Quote = quotes_sub[self.far_symbol]
            # 检查两个合约行情数据是否同步（时间戳差值<0.5秒）
            if self._check_data_sync(near_quote, far_quote):
                # 计算指标
                current_spread_indicator = self._klines_calculate(quotes_sub, klines_sub)
                if not current_spread_indicator:
                    return
                # 以上条件都满足，调用on_tick执行策略逻辑
                self.on_tick(near_quote, far_quote)

    def _check_data_sync(self, near_quote: Quote, far_quote: Quote) -> bool:
        """
        检查数据同步
        ----------
        Parameters
        near_quote : Quote  近月合约行情
        far_quote : Quote   远月合约行情
        -------
        Returns    bool     是否数据同步
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

    def _klines_calculate(self, quotes_sub: Dict[str, Quote], klines_sub: Dict[str, pd.DataFrame]):
        near_klines = klines_sub[self.near_symbol]
        far_klines = klines_sub[self.far_symbol]
        # 两个kline 都没变化就返回
        if (not self.gateway.tq_md_api.api.is_changing(near_klines)
                and not self.gateway.tq_md_api.api.is_changing(far_klines)):
            return
        # 确保有足够的数据
        if len(near_klines) < self.klines_windows or len(far_klines) < self.klines_windows:
            self.gateway.write_log(f"时间窗口不一致， near_klines: {len(near_klines)} far_klines: {len(far_klines)}")
            return

        # 计算盘口成本
        near_quote: Quote = quotes_sub[self.near_symbol]
        far_quote: Quote = quotes_sub[self.far_symbol]
        # 计算逻辑价差
        current_spread = near_quote.last_price - far_quote.last_price
        # 获取真实价差
        # 开仓时的盘口成本，以空差为例：  (近期买1价-远期卖1价) - (近期卖1价-远期买1价)   结果为负数就是成本，为正数就是利润
        # 盘口成本为负时，说明价差还可能会上涨，比如会到-10，所以等价差从高点开始回落，再下单是比较好的时机
        real_short_spread = near_quote.bid_price1 - far_quote.ask_price1
        real_long_spread = near_quote.ask_price1 - far_quote.bid_price1
        dynamic_spread_cost = self.static_spread_cost + abs(real_short_spread - real_long_spread) * 2

        # 获取两个klines的最新时间，如果不一致，则缝合数据
        near_kline_time = datetime.fromtimestamp(near_klines.datetime.iloc[-1] / 1e9)
        far_kline_time = datetime.fromtimestamp(far_klines.datetime.iloc[-1] / 1e9)

        # klines数据对齐的情况
        if near_kline_time == far_kline_time:

            # calulate_spread(near_klines, far_klines)
            # 获取窗口内价格
            near_close = near_klines.close.iloc[-self.klines_windows:]
            far_close = far_klines.close.iloc[-self.klines_windows:]
            # 计算价差
            spread = near_close - far_close
            # 计算均值和标准差
            mean = np.mean(spread)
            std = np.std(spread)
            # 计算上轨边界
            upper_bound = mean + max(self.klines_std_k * std, dynamic_spread_cost)   # 出于风控，不能低于THRESHOLD_DOWN
            # 计算下轨边界
            lower_bound = mean - max(self.klines_std_k * std, dynamic_spread_cost)
            # 存储前值
            self.current_spread_indicator = (mean, std, upper_bound, lower_bound)
            return self.current_spread_indicator

        else:
            self.gateway.write_log(f"---near_klines时间：{near_kline_time}, far_klines时间：{far_kline_time}， near_klines时间快")
            # 数据没对齐就用前值
            if self.current_spread_indicator is not None:
                return self.current_spread_indicator
            else:
                # 数据不存在，返回空
                return None

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

        # 检查不可交易状态，由RiskManager控制
        if not self.is_tradable:
            return

        try:
            # 计算价差
            # real_short_spread: 做空价差（可卖出价差）= 近月买价 - 远月卖价
            real_short_spread = near_quote.bid_price1 - far_quote.ask_price1
            # real_long_spread: 做多价差（可买入价差）= 近月卖价 - 远月买价
            real_long_spread = near_quote.ask_price1 - far_quote.bid_price1

            # 风控检查3：待成交订单检查
            # 如果有待成交订单，检查是否超时，然后返回等待订单成交或超时
            if self.pending_orders:
                self._check_pending_orders_timeout()
                return  # 有待成交订单，等待成交或超时

            # 获取当前持仓类型（"short_spread"|"long_spread"|None）
            position = self.global_position.get("spread_position", None)
            # 如果已有持仓，不允许开新仓（因为transaction_volume=1，只允许1手持仓）
            # 如果未持仓才进入寻找开仓机会
            if position is None:
                # 空仓检查开仓
                self._find_open_opportunity(real_short_spread, real_long_spread, near_quote, far_quote)
                return
            else:
                # 持仓检查平仓
                self._find_close_opportunity(real_short_spread, real_long_spread, near_quote, far_quote, position)


        except Exception as e:
            self.gateway.write_log(f"策略执行异常: {str(e)}")


    def _find_open_opportunity(self, real_short_spread: float, real_long_spread: float,
                               near_quote: Quote, far_quote: Quote) -> None:
        """
        检查开仓机会（在无持仓时调用）

        逻辑：
        1. 检查是否可以开仓（仓位控制，只允许1手持仓）
        2. 检查做空价差机会：real_short_spread > upper_band，价差过高时做空价差
        3. 检查做多价差机会：real_long_spread < lower_band，价差过低时做多价差

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

        (mean, std, upper_bound, lower_bound) = self.current_spread_indicator
        # 做空价差：价差过高（>105）
        # 卖出近月合约，买入远月合约，预期价差会回归到中轨（35）
        if real_short_spread > upper_bound:
            self._spread_open_short(near_quote, far_quote, real_short_spread)
            self.gateway.write_log(f"做空价差开仓: {real_short_spread} > {upper_bound}")
            return

        # 做多价差：价差过低（<-30）
        # 买入近月合约，卖出远月合约，预期价差会回归到中轨（35）
        if real_long_spread < lower_bound:
            self._spread_open_long(near_quote, far_quote, real_long_spread)
            self.gateway.write_log(f"做多价差开仓: {real_long_spread} < {lower_bound}")
            return

    def _find_close_opportunity(self, real_short_spread, real_long_spread, near_quote, far_quote, position):
        # 获取持仓类型
        position_type = position.get("position_type") if position else None
        # 获取技术指标
        (mean, std, upper_bound, lower_bound) = self.current_spread_indicator

        # 根据持仓状态执行相应操作
        if position_type == SHORT_SPREAD_TYPE:
            # # 持有做空价差持仓，检查是否平仓或止损
            open_spread = position["open_spread"]
            # 获取计算指标
            # 平仓：价差回归到中轨（<=35）
            # 做空价差盈利了，平仓获利
            if real_long_spread <= mean:
                self.gateway.write_log(f"做空价差回归: {real_long_spread} <= {mean}，平仓")
                self._spread_close_short(near_quote, far_quote)
                return

            # 风控止损：价差继续扩大（>开仓价+3个标准差）
            # 做空价差亏损了，止损平仓
            if real_long_spread > open_spread + std * 3:
                self.gateway.write_log(f"做空价差止损: {real_long_spread} > {open_spread} + {std * 3}，平仓")
                self._spread_close_short(near_quote, far_quote)
                return

            # 持有做空价差持仓，检查是否平仓或止损
            # self._manage_short_spread_position(real_long_spread, position)
        elif position_type == LONG_SPREAD_TYPE:
            # 获取开仓时的价差
            open_spread = position["open_spread"]
            # 平仓：价差回归到中轨（>=35）
            # 做多价差盈利了，平仓获利
            if real_short_spread >= mean:
                self.gateway.write_log(f"做多价差回归: {real_short_spread} >= {mean}，平仓")
                self._spread_close_long(near_quote, far_quote)
                return

            # 风控止损：价差继续下跌（<开仓价-50）
            # 做多价差亏损了，止损平仓
            if real_short_spread < open_spread - std * 3:
                self.gateway.write_log(f"做多价差止损: {real_short_spread} < {open_spread} - {std * 3}，平仓")
                self._spread_close_long(near_quote, far_quote)
                return

            # 持有多头价差持仓，检查是否平仓或止损
            # self._manage_long_spread_position(real_short_spread, position)

    def _spread_close_short(self, near_quote: Quote, far_quote: Quote) -> None:
        """
        发送平仓订单（同时平两条腿）

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
            order_type, near_price, far_price = get_market_price(
                near_quote, far_quote, self.exchange, LONG_SPREAD_TYPE)

            # 平近月空单
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=self.exchange,
                direction=Direction.LONG,
                type=order_type,
                volume=self.transaction_volume,
                price=near_price,
                offset=Offset.CLOSETODAY,
                reference=f"spread_close_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 平远月多单
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=self.exchange,
                direction=Direction.SHORT,
                type=order_type,
                volume=self.transaction_volume,
                price=far_price,
                offset=Offset.CLOSETODAY,
                reference=f"spread_close_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单（用于超时检查）
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": Direction.LONG.value,
                    "offset": Offset.CLOSETODAY.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_far  # 配对订单ID
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": Direction.SHORT.value,
                    "offset": Offset.CLOSETODAY.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_near  # 配对订单ID
                }

                self.gateway.write_log(f"平空差订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("平空差订单发送失败")

        except Exception as e:
            self.gateway.write_log(f"发送平空差仓订单异常: {str(e)}")

    def _spread_close_long(self, near_quote: Quote, far_quote: Quote) -> None:
        """
        发送平多差仓订单（同时平两条腿）

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
            order_type, near_price, far_price = get_market_price(
                near_quote, far_quote, self.exchange, SHORT_SPREAD_TYPE)

            # 平近月空单
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=self.exchange,
                direction=Direction.SHORT,
                type=order_type,
                volume=self.transaction_volume,
                price=near_price,
                offset=Offset.CLOSETODAY,
                reference=f"spread_close_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 平远月多单
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=self.exchange,
                direction=Direction.LONG,
                type=order_type,
                volume=self.transaction_volume,
                price=far_price,
                offset=Offset.CLOSETODAY,
                reference=f"spread_close_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单（用于超时检查）
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": Direction.SHORT.value,
                    "offset": Offset.CLOSETODAY.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_far  # 配对订单ID
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": Direction.LONG.value,
                    "offset": Offset.CLOSETODAY.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_near  # 配对订单ID
                }

                self.gateway.write_log(f"平多差订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("平多差订单发送失败")

        except Exception as e:
            self.gateway.write_log(f"发送平多差仓订单异常: {str(e)}")

    def _spread_open_short(self, near_quote: Quote, far_quote: Quote, spread: float) -> None:
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
            order_type, near_price, far_price = get_market_price(
                near_quote, far_quote, self.exchange, SHORT_SPREAD_TYPE)

            # 下单1：卖出近月合约（SHORT, OPEN）
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=self.exchange,
                direction=Direction.SHORT,
                type=order_type,
                volume=self.transaction_volume,
                price=near_price,
                offset=Offset.OPEN,
                reference=f"spread_short_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 下单2：买入远月合约（LONG, OPEN）
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=self.exchange,
                direction=Direction.LONG,
                type=order_type,
                volume=self.transaction_volume,
                price=far_price,
                offset=Offset.OPEN,
                reference=f"spread_short_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单（用于超时检查和状态跟踪）
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": Direction.SHORT.value,
                    "offset": Offset.OPEN.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_far  # 配对订单ID
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": Direction.LONG.value,
                    "offset": Offset.OPEN.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_near  # 配对订单ID
                }

                # 暂时记录持仓（实际要等订单成交，所以near_filled和far_filled都为False）
                # 这样在订单状态更新时可以更新这些标志
                self.global_position["spread_position"] = {
                    "position_type": SHORT_SPREAD_TYPE,  # 持仓类型
                    "open_spread": spread,        # 开仓时的价差
                    "open_time": datetime.now(),  # 开仓时间
                    "near_symbol": self.near_symbol,
                    "near_order_id": order_id_near,  # 近月订单ID
                    "near_price1": near_quote.bid_price1,  # 记录卖1价
                    "near_filled": False,  # 近月是否成交（初始为False）
                    "near_yd_volume": 0,  # 新开仓都是今仓，昨仓为0
                    "far_symbol": self.far_symbol,
                    "far_order_id": order_id_far,    # 远月订单ID
                    "far_price1": far_quote.ask_price1,  # 记录买1价
                    "far_filled": False,   # 远月是否成交（初始为False）
                    "far_yd_volume": 0     # 新开仓都是今仓，昨仓为0
                }
                self.gateway.write_log(f"做空价差开仓订单已发送: near_order:{order_id_near}, near_price1_bid: {near_quote.bid_price1}, "
                                       f"far_order:{order_id_far}, far_price1_ask: {far_quote.ask_price1} ")
            else:
                self.gateway.write_log("做空价差开仓失败")
                # 清理部分订单（如果有一个订单发送成功，另一个失败）
                if order_id_near:
                    self._cancel_order(order_id_near, False)
                if order_id_far:
                    self._cancel_order(order_id_near, False)
                if "spread_position" in self.global_position:
                    del self.global_position["spread_position"]

        except Exception as e:
            self.gateway.write_log(f"做空价差开仓异常: {str(e)}")

    def _spread_open_long(self, near_quote: Quote, far_quote: Quote, spread: float) -> None:
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
            order_type, near_price, far_price = get_market_price(
                near_quote, far_quote, self.exchange, LONG_SPREAD_TYPE)

            # 下单1：买入近月合约（LONG, OPEN）
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=self.exchange,
                direction=Direction.LONG,
                type=order_type,
                volume=self.transaction_volume,
                price=near_price,
                offset=Offset.OPEN,
                reference=f"spread_long_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 下单2：卖出远月合约（SHORT, OPEN）
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=self.exchange,
                direction=Direction.SHORT,
                type=order_type,
                volume=self.transaction_volume,
                price=far_price,
                offset=Offset.OPEN,
                reference=f"spread_long_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)
            # 返回订单id，如果发送失败返回""
            if order_id_near and order_id_far:
                # 记录待成交订单（用于超时检查和状态跟踪）
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": Direction.LONG.value,
                    "offset": Offset.OPEN.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_far  # 配对订单ID
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": Direction.SHORT.value,
                    "offset": Offset.OPEN.value,
                    "create_time": create_time,
                    "pair_order_id": order_id_near  # 配对订单ID
                }

                # 暂时记录持仓（实际要等订单成交，所以near_filled和far_filled都为False）
                self.global_position["spread_position"] = {
                    "position_type": LONG_SPREAD_TYPE,  # 持仓类型
                    "open_spread": spread,        # 开仓时的价差
                    "open_time": datetime.now(),  # 开仓时间
                    "near_symbol": self.near_symbol,
                    "near_order_id": order_id_near,  # 近月订单ID
                    "near_price1": near_quote.ask_price1,  # 记录买1价
                    "near_filled": False,  # 近月是否成交（初始为False）
                    "near_yd_volume": 0,  # 新开仓都是今仓，昨仓为0
                    "far_symbol": self.far_symbol,
                    "far_order_id": order_id_far,    # 远月订单ID
                    "far_price1": far_quote.bid_price1,  # 记录卖1价
                    "far_filled": False,   # 远月是否成交（初始为False）
                    "far_yd_volume": 0     # 新开仓都是今仓，昨仓为0
                }

                self.gateway.write_log(f"做多价差开仓订单已发送: near_order:{order_id_near}, near_price1_ask: {near_quote.ask_price1}, "
                                       f"far_order:{order_id_far}, far_price1_bid: {far_quote.bid_price1} ")
            else:
                self.gateway.write_log("做多价差开仓失败")
                # 清理部分订单（如果有一个订单发送成功，另一个失败）
                if order_id_near:
                    self._cancel_order(order_id_near, False)
                if order_id_far:
                    self._cancel_order(order_id_near, False)
                if "spread_position" in self.global_position:
                    del self.global_position["spread_position"]

        except Exception as e:
            self.gateway.write_log(f"做多价差开仓异常: {str(e)}")

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

                # 更新global_position中的成交标志
                position = self.global_position.get("spread_position")
                if position:
                    if position.get("near_order_id") == vt_orderid:
                        # 近月订单成交，设置near_filled为True
                        position["near_filled"] = True
                    elif position.get("far_order_id") == vt_orderid:
                        # 远月订单成交，设置far_filled为True
                        position["far_filled"] = True

                # 从待成交订单中移除
                if vt_orderid in self.pending_orders:
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
        """注册回调事件"""
        self.gateway.event_engine.register(EVENT_ERROR_ORDER, self.on_order_exception)
        self.gateway.event_engine.register(EVENT_UPDATE_QUOTE, self.on_subscribe_quote)

    def on_order_exception(self, event: Event) -> None:
        error_event_data: dict = event.data
        data: dict = error_event_data[0]
        error: dict = error_event_data[1]
        reqid: int = error_event_data[2]
        self.gateway.write_log(f"订单错误：{data} 错误信息：{error}  reqid:{reqid}")
        # if error and error["ErrorID"]==1011:
        #     self.pending_orders

    def on_subscribe_quote(self, event: Event) -> None:
        self.spread_quotes.append(self.gateway.tq_md_api.quotes[self.near_symbol])
        self.spread_quotes.append(self.gateway.tq_md_api.quotes[self.far_symbol])
        self.spread_klines.append(self.gateway.tq_md_api.klines[self.near_symbol])
        self.spread_klines.append(self.gateway.tq_md_api.klines[self.far_symbol])

    def on_position_update(self, positions: list[PositionData]) -> None:
        """持仓更新回调（从TdApi的onRspQryInvestorPosition调用）

        Parameters
        ----------
        positions : list
            持仓列表
        """
        try:
            # 如果已经加载过持仓，就不需要再处理了
            # if self.position_loaded:
            #     return

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

                existing_position = self.global_position.get("spread_position")

                if not self.position_loaded:
                    self.gateway.write_log("检测到做空价差持仓，已恢复策略状态")

                if existing_position:
                    # 持仓已存在，只更新必要字段，保留原有信息
                    existing_position.update({
                        "near_volume": near_short_positions[0].volume,
                        "near_yd_volume": near_short_positions[0].yd_volume,
                        "far_volume": far_long_positions[0].volume,
                        "far_yd_volume": far_long_positions[0].yd_volume,
                    })
                else:
                    # 首次加载，创建完整持仓信息
                    self.global_position["spread_position"] = {
                        "position_type": SHORT_SPREAD_TYPE,  # 持仓类型
                        "open_time": datetime.now(),  # 使用当前时间作为开仓时间
                        "open_spread": near_short_positions[0].price - far_long_positions[0].price,  # 原始开仓价是近期买1-远期卖1 或者近期卖1-远期买1，这里只能用两个实际成交价相减
                        "near_symbol": near_short_positions[0].symbol,
                        "near_order_id": "",
                        "near_filled": True,
                        "near_price1": near_short_positions[0].price,
                        "near_volume": near_short_positions[0].volume,
                        "near_yd_volume": near_short_positions[0].yd_volume,  # 昨仓数量
                        "far_symbol": far_long_positions[0].symbol,
                        "far_order_id": "",
                        "far_filled": True,
                        "far_price1": far_long_positions[0].price,
                        "far_volume": far_long_positions[0].volume,
                        "far_yd_volume": far_long_positions[0].yd_volume,  # 昨仓数量
                    }
                position_restored = True

            # 检查是否是做多价差持仓（near多 + far空）
            elif (near_long_positions and far_short_positions and
                  len(near_long_positions) == self.transaction_volume and
                  len(far_short_positions) == self.transaction_volume):

                existing_position = self.global_position.get("spread_position")

                if not self.position_loaded:
                    self.gateway.write_log("检测到做多价差持仓，已恢复策略状态")

                if existing_position:
                    # 持仓已存在，只更新必要字段，保留原有信息
                    existing_position.update({
                        "near_volume": near_long_positions[0].volume,
                        "near_yd_volume": near_long_positions[0].yd_volume,
                        "far_volume": far_short_positions[0].volume,
                        "far_yd_volume": far_short_positions[0].yd_volume,
                    })
                else:
                    # 首次加载，创建完整持仓信息
                    self.global_position["spread_position"] = {
                        "position_type": LONG_SPREAD_TYPE,  # 持仓类型
                        "open_time": datetime.now(),  # 使用当前时间作为开仓时间
                        "open_spread": near_long_positions[0].price - far_short_positions[0].price,  # 无法获取原始开仓价差，设为0
                        "near_symbol": near_long_positions[0].symbol,
                        "near_order_id": "",
                        "near_filled": True,
                        "near_price1": near_long_positions[0].price,
                        "near_volume": near_long_positions[0].volume,
                        "near_yd_volume": near_long_positions[0].yd_volume,  # 昨仓数量
                        "far_symbol": far_short_positions[0].symbol,
                        "far_order_id": "",
                        "far_filled": True,
                        "far_price1": far_short_positions[0].price,
                        "far_volume": far_short_positions[0].volume,
                        "far_yd_volume": far_short_positions[0].yd_volume,  # 昨仓数量
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

    def _cancel_order(self, vt_orderid: str, del_pending: bool = True) -> None:
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
                if del_pending:
                    del self.pending_orders[vt_orderid]

        except Exception as e:
            self.gateway.write_log(f"撤销订单异常: {str(e)}")

    def _handle_partial_fill(self) -> None:
        """处理单腿成交情况"""
        try:
            position = self.global_position.get("spread_position")
            if not position:
                return

            position_type = position.get("position_type")
            if not position_type:
                return

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
            if position_type == SHORT_SPREAD_TYPE:
                if leg == "near":
                    # 平near空头
                    self._send_emergency_close_order(self.near_symbol, Direction.LONG)
                else:
                    # 平far多头
                    self._send_emergency_close_order(self.far_symbol, Direction.SHORT)
            elif position_type == LONG_SPREAD_TYPE:
                if leg == "near":
                    # 平near多头
                    self._send_emergency_close_order(self.near_symbol, Direction.SHORT)
                else:
                    # 平far空头
                    self._send_emergency_close_order(self.far_symbol, Direction.LONG)

            # 清空持仓
            if "spread_position" in self.global_position:
                del self.global_position["spread_position"]

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
        # order_type, near_price, far_price = get_market_price(
        #     near_quote, far_quote, self.exchange, LONG_SPREAD_TYPE)
        if symbol == self.near_symbol:
            close_quote = self.spread_quotes[0]
        elif symbol == self.far_symbol:
            close_quote = self.spread_quotes[1]
        else:
            return

        if self.exchange in [Exchange.SHFE, Exchange.INE]:
            order_type = OrderType.LIMIT
            if direction == Direction.LONG:
                close_price = close_quote.upper_limit - (close_quote.upper_limit - close_quote.pre_settlement) * 0.1
            elif direction == Direction.SHORT:
                close_price = close_quote.lower_limit + (close_quote.pre_settlement - close_quote.lower_limit) * 0.1
            else:
                return
        else:
            order_type = OrderType.MARKET
            close_price = 0.0


        try:
            contract = self.gateway.symbol_contract_map_tqsdk.get(symbol)
            if not contract:
                return
            # emergency_close是单腿成交触发，所以只平今仓
            req = OrderRequest(
                symbol=symbol,
                exchange=contract.exchange,
                direction=direction,
                type=order_type,
                volume=self.transaction_volume,
                price=close_price,
                offset=Offset.CLOSETODAY,
                reference=f"emergency_close_{symbol}"
            )
            order_id = self.gateway.send_order(req)
            self.gateway.write_log(f"紧急平仓订单已发送: {symbol} {direction.value} {order_id}")

        except Exception as e:
            self.gateway.write_log(f"发送紧急平仓订单异常: {str(e)}")


    def close_position(self, force: bool = False) -> None:
        """
        平仓价差持仓

        逻辑：
        1. 获取当前持仓（global_position["spread_position"]）
        2. 根据持仓的 position_type 确定平仓方向：
           - 做空价差持仓：买入近月，卖出远月（LONG, SHORT）
           - 做多价差持仓：卖出近月，买入远月（SHORT, LONG）
        3. 调用_send_close_orders发送平仓订单
        4. 清空持仓

        Parameters
        ----------
        force : bool
            是否强制平仓
        """
        try:
            # 获取当前持仓
            position = self.global_position.get("spread_position")
            if not position:
                return  # 无持仓

            position_type = position.get("position_type")
            if not position_type:
                return

            near_quote = self.spread_quotes[0]
            far_quote = self.spread_quotes[1]

            if position_type == SHORT_SPREAD_TYPE:
                # 平做空价差：买入近月，卖出远月
                self._spread_close_short(near_quote, far_quote)
            elif position_type == LONG_SPREAD_TYPE:
                # 平做多价差：卖出近月，买入远月
                self._spread_close_long(near_quote, far_quote)

            # 清空持仓
            del self.global_position["spread_position"]
            self.gateway.write_log("价差持仓已平仓")

        except Exception as e:
            self.gateway.write_log(f"平仓异常: {str(e)}")


class AgSpreadStrategy(SpreadTradingStrategy):
    """
    白银跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq"):
        super().__init__(gateway, "ag2604", "ag2606", Exchange.SHFE)
        # super().__init__(gateway, "sn2603", "sn2604", Exchange.SHFE)
        # 设置白银特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.min_profit_points = 10    # 基本利润（最小盈利）
        self.slippage_points = 3 * 4   # 做一次差价就是4次下单，一次滑点设为3
        self.commission_point = 16     # 做一次差价开平的手续费成本,240，一跳15元
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60  # kline的请求周期


class NiSpreadStrategy(SpreadTradingStrategy):
    """
    白银跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq"):
        # super().__init__(gateway, "ag2604", "ag2606", Exchange.SHFE)
        # super().__init__(gateway, "sn2603", "sn2604", Exchange.SHFE)
        super().__init__(gateway, "ni2603", "ni2605", Exchange.SHFE)
        # 设置白银特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.min_profit_points = 10    # 基本利润（最小盈利）
        self.slippage_points = 3 * 4   # 做一次差价就是4次下单，一次滑点设为3
        self.commission_point = 2     # 做一次差价开平的手续费成本,12，一跳10元
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60  # kline的请求周期

class SnSpreadStrategy(SpreadTradingStrategy):
    """
    白银跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq"):
        super().__init__(gateway, "sn2603", "sn2604", Exchange.SHFE)
        # 设置白银特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.min_profit_points = 10    # 基本利润（最小盈利）
        self.slippage_points = 50 * 4   # 做一次差价就是4次下单，一次滑点设为50
        self.commission_point = 2     # 做一次差价开平的手续费成本,12，一跳10元
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60  # kline的请求周期