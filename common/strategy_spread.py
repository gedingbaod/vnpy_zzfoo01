"""
跨期套利策略模块
包含白银跨期套利策略的实现
"""

from __future__ import annotations

import math
import sys
import threading
from datetime import datetime, timedelta, time
from time import sleep
from threading import Thread
from typing import Any, Union, Optional, TYPE_CHECKING, Dict

from tqsdk.objs import Quote
from zmq.backend import second

from common.vnpy_time import split_cross_day_time
from vnpy.trader.constant import Direction, Offset, Exchange, OrderType, Status
from vnpy.trader.object import OrderRequest, CancelRequest, ContractData, SubscribeRequest

if TYPE_CHECKING:
    from common.gateway_tq import BaseGatewayTq

# 其他常量
MAX_FLOAT = sys.float_info.max
# 收盘前多少分钟停止交易
CLOSE_BEFORE_MINUTE = 15


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
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.max_spread_cost = 25      # 最大买卖价差成本
        self.stop_loss_points = 50     # 止损点数
        self.max_price_limit_ratio = 0.8  # 涨跌停限制比例

        # 持仓状态
        self.spread_position: dict = {}  # 持仓状态 {"short_spread"|"long_spread": {...}}

        # 待成交订单
        self.pending_orders: dict = {}  # {order_id: {"symbol": "", "direction": "", "offset": "", "create_time": timestamp}}

        # 订单状态映射
        self.order_status_map: dict = {}  # {order_id: status}

        # 状态标志
        self.is_closing_time: bool = False  # 是否处于收盘前5分钟
        self.close_time_warned: bool = False  # 是否已经发出收盘警告
        self.thread_active: bool = True  # 策略是否激活

        # 启动收盘时间检查线程（每分钟检查一次）
        # 创建Event对象，用于控制等待中断
        self.interrupt_event = threading.Event()
        self.closing_check_thread = Thread(target=self._check_closing_time_loop, daemon=True)
        self.closing_check_thread.start()

    # await_update之后调用这个函数检查是否可以交易
    def check_and_run(self, quotes_sub: Dict[str, Quote]) -> None:
        if self.near_symbol in quotes_sub and self.far_symbol in quotes_sub:
            near_quote: Quote = quotes_sub['ag2604']
            far_quote: Quote = quotes_sub['ag2606']
            if self._check_data_sync(near_quote, far_quote):
                self.on_tick(near_quote, far_quote)

    def on_tick(self, near_quote: Quote, far_quote: Quote) -> None:
        """
        行情更新回调，在策略主循环中调用

        Parameters
        ----------
        near_quote : Quote
            近月合约行情
        far_quote : Quote
            远月合约行情
        """

        # 如果处于收盘时间，不执行任何交易逻辑
        if self.is_closing_time:
            return

        try:
            # 计算价差
            real_short_spread = near_quote.bid_price1 - far_quote.ask_price1
            real_long_spread = near_quote.ask_price1 - far_quote.bid_price1
            spread_cost = (near_quote.ask_price1 - near_quote.bid_price1) + \
                         (far_quote.ask_price1 - far_quote.bid_price1)

            # 检查买卖价差成本
            if spread_cost > self.max_spread_cost:
                self.gateway.write_log(f"买卖价差成本过大: {spread_cost}，超过{self.max_spread_cost}，停止交易")
                return

            # 检查涨跌停
            if self._check_price_limit(near_quote, far_quote):
                return  # 接近涨跌停，停止交易

            # 检查是否有待成交订单
            if self.pending_orders:
                self._check_pending_orders_timeout()
                return  # 有待成交订单，等待成交或超时

            # 获取当前持仓类型
            position_type = self._get_position_type()

            # 持仓管理
            if position_type == "short_spread":
                self._manage_short_spread_position(real_long_spread)
            elif position_type == "long_spread":
                self._manage_long_spread_position(real_short_spread)
            elif position_type is None and not self.is_closing_time:
                # 无持仓，检查开仓机会
                self._check_open_opportunity(real_short_spread, real_long_spread,near_quote, far_quote)
        except Exception as e:
            self.gateway.write_log(f"策略执行异常: {str(e)}")


    def _check_open_opportunity(self, real_short_spread: float, real_long_spread: float,
                                near_quote: Quote, far_quote: Quote) -> None:
        """检查开仓机会"""
        # 做空价差：价差过高
        if real_short_spread > self.upper_band:
            self.open_short_spread(near_quote, far_quote, real_short_spread)
            self.gateway.write_log(f"做空价差开仓: {real_short_spread} > {self.upper_band}")
            return

        # 做多价差：价差过低
        if real_long_spread < self.lower_band:
            self.open_long_spread(near_quote, far_quote, real_long_spread)
            self.gateway.write_log(f"做多价差开仓: {real_long_spread} < {self.lower_band}")
            return

    def _manage_short_spread_position(self, current_spread: float) -> None:
        """管理做空价差持仓"""
        if "short_spread" not in self.spread_position:
            return

        open_spread = self.spread_position["short_spread"]["open_spread"]

        # 平仓：价差回归到中轨
        if current_spread <= self.middle_band:
            self.gateway.write_log(f"做空价差回归: {current_spread} <= {self.middle_band}，平仓")
            self.close_position()
            return

        # 止损：价差继续扩大
        if current_spread > open_spread + self.stop_loss_points:
            self.gateway.write_log(f"做空价差止损: {current_spread} > {open_spread + self.stop_loss_points}，平仓")
            self.close_position()
            return

    def _manage_long_spread_position(self, current_spread: float) -> None:
        """管理做多价差持仓"""
        if "long_spread" not in self.spread_position:
            return

        open_spread = self.spread_position["long_spread"]["open_spread"]

        # 平仓：价差回归到中轨
        if current_spread >= self.middle_band:
            self.gateway.write_log(f"做多价差回归: {current_spread} >= {self.middle_band}，平仓")
            self.close_position()
            return

        # 止损：价差继续下跌
        if current_spread < open_spread - self.stop_loss_points:
            self.gateway.write_log(f"做多价差止损: {current_spread} < {open_spread - self.stop_loss_points}，平仓")
            self.close_position()
            return

    def open_short_spread(self, near_quote: Quote, far_quote: Quote, spread: float) -> None:
        """
        开做空价差（卖近买远）

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

            # 卖出near（开仓）
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

            # 买入far（开仓）
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
                # 记录待成交订单
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": "SHORT",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_far
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": "LONG",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_near
                }

                # 暂时记录持仓（实际要等订单成交）
                self.spread_position["short_spread"] = {
                    "open_spread": spread,
                    "open_time": datetime.now(),
                    "near_order_id": order_id_near,
                    "far_order_id": order_id_far,
                    "near_filled": False,
                    "far_filled": False
                }

                self.gateway.write_log(f"做空价差开仓订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("做空价差开仓失败")
                # 清理部分订单
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

            # 买入near（开仓）
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

            # 卖出far（开仓）
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
                # 记录待成交订单
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": "LONG",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_far
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": "SHORT",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_near
                }

                # 暂时记录持仓（实际要等订单成交）
                self.spread_position["long_spread"] = {
                    "open_spread": spread,
                    "open_time": datetime.now(),
                    "near_order_id": order_id_near,
                    "far_order_id": order_id_far,
                    "near_filled": False,
                    "far_filled": False
                }

                self.gateway.write_log(f"做多价差开仓订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("做多价差开仓失败")
                # 清理部分订单
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

        Parameters
        ----------
        force : bool
            是否强制平仓
        """
        try:
            position_type = self._get_position_type()
            if not position_type:
                return

            if position_type == "short_spread":
                # 平做空价差：买near卖far
                self._send_close_orders(Direction.LONG, Direction.SHORT)
            elif position_type == "long_spread":
                # 平做多价差：卖near买far
                self._send_close_orders(Direction.SHORT, Direction.LONG)

            # 清空持仓
            self.spread_position.clear()
            self.gateway.write_log("价差持仓已平仓")

        except Exception as e:
            self.gateway.write_log(f"平仓异常: {str(e)}")

    def _send_close_orders(self, near_direction: Direction, far_direction: Direction) -> None:
        """发送平仓订单"""
        try:
            near_contract = self.gateway.symbol_contract_map_tqsdk.get(self.near_symbol)
            far_contract = self.gateway.symbol_contract_map_tqsdk.get(self.far_symbol)

            if not near_contract or not far_contract:
                self.gateway.write_log("未找到合约信息")
                return

            # near平仓
            req_near = OrderRequest(
                symbol=self.near_symbol,
                exchange=near_contract.exchange,
                direction=near_direction,
                type=OrderType.MARKET,
                volume=self.transaction_volume,
                price=0,
                offset=Offset.CLOSETODAY,
                reference=f"spread_close_near_{self.near_symbol}"
            )
            order_id_near = self.gateway.send_order(req_near)

            # far平仓
            req_far = OrderRequest(
                symbol=self.far_symbol,
                exchange=far_contract.exchange,
                direction=far_direction,
                type=OrderType.MARKET,
                volume=self.transaction_volume,
                price=0,
                offset=Offset.CLOSETODAY,
                reference=f"spread_close_far_{self.far_symbol}"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": self.near_symbol,
                    "direction": near_direction.value,
                    "offset": "CLOSETODAY",
                    "create_time": create_time,
                    "pair_order_id": order_id_far
                }
                self.pending_orders[order_id_far] = {
                    "symbol": self.far_symbol,
                    "direction": far_direction.value,
                    "offset": "CLOSETODAY",
                    "create_time": create_time,
                    "pair_order_id": order_id_near
                }

                self.gateway.write_log(f"平仓订单已发送: {order_id_near}, {order_id_far}")
            else:
                self.gateway.write_log("平仓订单发送失败")

        except Exception as e:
            self.gateway.write_log(f"发送平仓订单异常: {str(e)}")

    def on_order_status_update(self, vt_orderid: str, status: Status) -> None:
        """
        订单状态更新回调（从Gateway调用）

        Parameters
        ----------
        vt_orderid : str
            订单ID
        status : Status
            订单状态
        """
        try:
            # 更新订单状态映射
            self.order_status_map[vt_orderid] = status

            # 检查是否是待成交订单
            if vt_orderid not in self.pending_orders:
                return

            order_info = self.pending_orders[vt_orderid]
            pair_order_id = order_info.get("pair_order_id")

            # 订单完全成交
            if status == Status.ALLTRADED:
                self.gateway.write_log(f"订单完全成交: {vt_orderid}")

                # 更新持仓状态
                position_type = self._get_position_type()
                if position_type and position_type in self.spread_position:
                    position = self.spread_position[position_type]
                    if position.get("near_order_id") == vt_orderid:
                        position["near_filled"] = True
                    elif position.get("far_order_id") == vt_orderid:
                        position["far_filled"] = True

                # 从待成交订单中移除
                del self.pending_orders[vt_orderid]

                # 检查配对订单是否也成交
                if pair_order_id and pair_order_id not in self.pending_orders:
                    # 两条腿都成交，开仓完成
                    self.gateway.write_log(f"价差订单对成交完成")

            # 订单被拒绝或撤销
            elif status in [Status.REJECTED, Status.CANCELLED]:
                self.gateway.write_log(f"订单{status.value}: {vt_orderid}")

                # 从待成交订单中移除
                del self.pending_orders[vt_orderid]

                # 处理单腿成交
                self._handle_partial_fill()

        except Exception as e:
            self.gateway.write_log(f"订单状态更新异常: {str(e)}")

    def stop(self) -> None:
        """停止策略"""
        self.thread_active = False
        # 打断interrupt_event.wait()等待
        self.interrupt_event.set()
        # 等待收盘检查线程结束
        if hasattr(self, 'closing_check_thread') and self.closing_check_thread:
            self.closing_check_thread.join(timeout=3)

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
            if near_quote.datetime=='' or near_quote.datetime=='':
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
        contract_near: ContractData | None = self.gateway.symbol_contract_map_tqsdk.get(self.near_symbol, None)
        if not contract_near:
            return
        contract_far: ContractData | None = self.gateway.symbol_contract_map_tqsdk.get(self.far_symbol, None)
        if not contract_near:
            return

        req_near: SubscribeRequest = SubscribeRequest(
            symbol=contract_near.symbol, exchange=contract_near.exchange
        )
        req_far: SubscribeRequest = SubscribeRequest(
            symbol=contract_far.symbol, exchange=contract_far.exchange
        )

        self.gateway.subscribe(req_near)
        self.gateway.subscribe(req_far)
        self.gateway.write_log(f"订阅跨期合约{self.near_symbol}和{self.far_symbol}")

