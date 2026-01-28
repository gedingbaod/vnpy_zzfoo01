"""
TQSDK行情API通用模块
可用于各种gateway的TQSDK行情接入
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, time
from time import sleep
from threading import Thread
from typing import Any, Union, Optional

from tqsdk.objs import Quote
from tqsdk import TqApi, TqAuth

from common.gateway_tq import BaseGatewayTq
from vnpy.event.engine import EventEngine
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
    from common.vnpy_time import datetime_format
    TQ_AUTH_AVAILABLE = True
except ImportError:
    TQ_AUTH_AVAILABLE = False
    tq_auth = None

# 全局合约映射字典（需要在主程序中定义）
# symbol_contract_map: dict[str, ContractData] = {}

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
        self.quotes: dict[str, Any] = {}  # 保存行情引用 {symbol: quote}

        # 测试下单相关
        self.order_placed: bool = False  # 是否已经下过单
        self.order_ids: list[str] = []  # 记录订单ID
        self.traded_vt_orderids: set = set()  # 已成交的订单ID集合

        # 跨期套利相关
        self.spread_position: dict = {}  # 持仓状态 {"short_spread"|"long_spread": {near, far, open_time, open_spread}}
        self.pending_orders: dict = {}  # 待成交订单 {order_id: {"symbol": "", "direction": "", "offset": "", "create_time": timestamp}}
        self.order_status_map: dict = {}  # 订单状态映射 {order_id: status}
        self.is_closing_time: bool = False  # 是否处于收盘前5分钟
        self.close_time_warned: bool = False  # 是否已经发出收盘警告

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

            # 启动行情接收线程
            self.active = True
            self.thread = Thread(target=self._run)
            self.thread.start()
            self.gateway.write_log("TQSDK行情线程启动")

            # 订阅之前已经订阅的合约
            for symbol in self.subscribed:
                self._subscribe_symbol(symbol)

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

            # 保存行情引用
            self.quotes[symbol] = quote
            self.gateway.write_log(f"TQSDK订阅行情成功：{tq_symbol}")

        except Exception as e:
            self.gateway.write_log(f"TQSDK订阅行情失败 [{tq_symbol}]：{str(e)}")

    def _run(self) -> None:
        """行情接收线程"""
        while self.active:
            try:
                # 等待行情推送
                self.api.wait_update()

                # 处理所有已订阅合约的行情
                for symbol, quote in self.quotes.items():
                    # 处理行情数据（显示在界面上）
                    self._process_tick(symbol, quote)

                # 检查是否有ag2604和ag2606的行情，执行跨期套利策略
                if 'ag2604' in self.quotes and 'ag2606' in self.quotes:
                    near_quote: Quote = self.quotes['ag2604']
                    far_quote: Quote = self.quotes['ag2606']
                    if (near_quote.last_price and far_quote.last_price
                            and near_quote.datetime == far_quote.datetime):
                        self._spread_ag2604_ag2606(near_quote, far_quote)
            except KeyboardInterrupt:
                print("主线程捕获到中断，正在退出...")
            except Exception as e:
                if self.active:
                    self.gateway.write_log(f"TQSDK行情处理异常：{str(e)}")
                break

        # 退出循环后关闭API
        print("wait_update循环退出")
        # if self.api:
        #     try:
        #         self.api.close()
        #         self.gateway.write_log("TQSDK连接关闭")
        #     except Exception:
        #         self.gateway.write_log(f"TQSDK行情处理异常：{str(e)}")
        #     self.api = None

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

    # ==================== 跨期套利策略 ====================

    def _spread_ag2604_ag2606(self, near_quote: Quote, far_quote: Quote) -> None:
        """白银跨期套利策略"""
        # 策略参数
        ag_upper_band = 100
        ag_middle_band = 35
        ag_lower_band = -30
        transaction_volume = 1
        order_timeout = 0.8
        data_sync_tolerance = 0.5
        max_price_limit_ratio = 0.8
        max_spread_cost = 25
        stop_loss_points = 50

        try:
            # 1. 检查数据同步
            time_diff = abs(near_quote.datetime - far_quote.datetime) / 1e9  # 转换为秒
            if time_diff > data_sync_tolerance:
                return  # 数据不同步，不交易

            # 2. 检查收盘时间
            if self._is_closing_time(near_quote):
                self.is_closing_time = True
                if not self.close_time_warned:
                    self.gateway.write_log("进入收盘前5分钟，停止新开仓")
                    self.close_time_warned = True
                # 强制平仓
                if self.spread_position:
                    self.gateway.write_log("收盘前强制平仓")
                    self._close_spread_position(force=True)
                return

            # 3. 计算价差
            real_short_spread = near_quote.bid_price1 - far_quote.ask_price1
            real_long_spread = near_quote.ask_price1 - far_quote.bid_price1
            spread_cost = (near_quote.ask_price1 - near_quote.bid_price1) + \
                         (far_quote.ask_price1 - far_quote.bid_price1)

            # 4. 检查买卖价差成本
            if spread_cost > max_spread_cost:
                self.gateway.write_log(f"买卖价差成本过大: {spread_cost}，超过{max_spread_cost}，停止交易")
                return

            # 5. 检查涨跌停
            if self._check_price_limit(near_quote, far_quote, max_price_limit_ratio):
                return  # 接近涨跌停，停止交易

            # 6. 检查是否有待成交订单
            if self.pending_orders:
                self._check_pending_orders_timeout(order_timeout)
                return  # 有待成交订单，等待成交或超时

            # 7. 获取当前持仓
            position_type = self._get_current_position_type()

            # 8. 持仓管理
            if position_type == "short_spread":
                # 持有做空价差，检查平仓或止损
                current_spread = real_long_spread
                open_spread = self.spread_position["short_spread"]["open_spread"]

                # 平仓：价差回归到中轨
                if current_spread <= ag_middle_band:
                    self.gateway.write_log(f"做空价差回归: {current_spread} <= {ag_middle_band}，平仓")
                    self._close_spread_position()
                    return

                # 止损：价差继续扩大
                if current_spread > open_spread + stop_loss_points:
                    self.gateway.write_log(f"做空价差止损: {current_spread} > {open_spread + stop_loss_points}，平仓")
                    self._close_spread_position()
                    return

            elif position_type == "long_spread":
                # 持有多头价差，检查平仓或止损
                current_spread = real_short_spread
                open_spread = self.spread_position["long_spread"]["open_spread"]

                # 平仓：价差回归到中轨
                if current_spread >= ag_middle_band:
                    self.gateway.write_log(f"做多价差回归: {current_spread} >= {ag_middle_band}，平仓")
                    self._close_spread_position()
                    return

                # 止损：价差继续下跌
                if current_spread < open_spread - stop_loss_points:
                    self.gateway.write_log(f"做多价差止损: {current_spread} < {open_spread - stop_loss_points}，平仓")
                    self._close_spread_position()
                    return

            elif position_type is None and not self.is_closing_time:
                # 无持仓，检查开仓机会
                # 做空价差：价差过高
                if real_short_spread > ag_upper_band:
                    self.gateway.write_log(f"做空价差开仓: {real_short_spread} > {ag_upper_band}")
                    self._open_short_spread(near_quote, far_quote, real_short_spread, transaction_volume)
                    return

                # 做多价差：价差过低
                if real_long_spread < ag_lower_band:
                    self.gateway.write_log(f"做多价差开仓: {real_long_spread} < {ag_lower_band}")
                    self._open_long_spread(near_quote, far_quote, real_long_spread, transaction_volume)
                    return

        except Exception as e:
            self.gateway.write_log(f"跨期套利策略异常: {str(e)}")

    def _is_closing_time(self, quote: Quote) -> bool:
        """判断是否处于收盘前5分钟"""
        # 从Quote.trading_time获取交易时段
        trading_time = quote.trading_time  # 格式: "9:00-15:00 21:00-2:30"
        current_time = datetime.now().time()

        # 解析交易时段
        periods = trading_time.split()
        for period in periods:
            start_str, end_str = period.split('-')
            start_time = time.fromisoformat(start_str)
            end_time = time.fromisoformat(end_str)

            # 判断是否在收盘前5分钟
            if end_time >= time(2, 0):  # 夜盘
                closing_time = (datetime.combine(datetime.today(), end_time) -
                              timedelta(minutes=5)).time()
                if closing_time <= current_time <= end_time:
                    return True
            else:  # 日盘
                closing_time = (datetime.combine(datetime.today(), end_time) -
                              timedelta(minutes=5)).time()
                if closing_time <= current_time <= end_time:
                    return True

        return False

    def _check_price_limit(self, near_quote: Quote, far_quote: Quote, ratio: float) -> bool:
        """检查是否接近涨跌停"""
        try:
            # 计算near合约的涨跌停幅度
            near_upper_move = (near_quote.upper_limit - near_quote.pre_settlement) * ratio
            near_lower_move = (near_quote.pre_settlement - near_quote.lower_limit) * ratio

            # 计算far合约的涨跌停幅度
            far_upper_move = (far_quote.upper_limit - far_quote.pre_settlement) * ratio
            far_lower_move = (far_quote.pre_settlement - far_quote.lower_limit) * ratio

            # 检查当前价格变动
            near_change = near_quote.last_price - near_quote.pre_settlement
            far_change = far_quote.last_price - far_quote.pre_settlement

            # 如果接近涨跌停，返回True
            if near_change >= near_upper_move or near_change <= -near_lower_move:
                self.gateway.write_log(f"ag2604接近涨跌停，停止交易")
                return True
            if far_change >= far_upper_move or far_change <= -far_lower_move:
                self.gateway.write_log(f"ag2606接近涨跌停，停止交易")
                return True

            return False
        except Exception as e:
            self.gateway.write_log(f"检查涨跌停异常: {str(e)}")
            return False

    def _get_current_position_type(self) -> Optional[str]:
        """获取当前持仓类型"""
        if "short_spread" in self.spread_position:
            return "short_spread"
        elif "long_spread" in self.spread_position:
            return "long_spread"
        else:
            return None

    def _open_short_spread(self, near_quote: Quote, far_quote: Quote,
                          spread: float, volume: int) -> None:
        """开做空价差（卖near买far）"""
        try:
            # 获取合约信息
            near_contract = self.gateway.symbol_contract_map_tqsdk.get("ag2604")
            far_contract = self.gateway.symbol_contract_map_tqsdk.get("ag2606")

            if not near_contract or not far_contract:
                self.gateway.write_log("未找到合约信息")
                return

            # 卖出near（开仓）
            req_near = OrderRequest(
                symbol="ag2604",
                exchange=near_contract.exchange,
                direction=Direction.SHORT,
                type=OrderType.MARKET,
                volume=volume,
                price=0,
                offset=Offset.OPEN,
                reference="spread_short_near"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 买入far（开仓）
            req_far = OrderRequest(
                symbol="ag2606",
                exchange=far_contract.exchange,
                direction=Direction.LONG,
                type=OrderType.MARKET,
                volume=volume,
                price=0,
                offset=Offset.OPEN,
                reference="spread_short_far"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": "ag2604",
                    "direction": "SHORT",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_far
                }
                self.pending_orders[order_id_far] = {
                    "symbol": "ag2606",
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
                    self.gateway.cancel_order(
                        CancelRequest(orderid=order_id_near.split('.')[-1],
                                     symbol="ag2604",
                                     exchange=near_contract.exchange)
                    )
                if order_id_far:
                    self.gateway.cancel_order(
                        CancelRequest(orderid=order_id_far.split('.')[-1],
                                     symbol="ag2606",
                                     exchange=far_contract.exchange)
                    )
                self.spread_position.clear()

        except Exception as e:
            self.gateway.write_log(f"做空价差开仓异常: {str(e)}")

    def _open_long_spread(self, near_quote: Quote, far_quote: Quote,
                         spread: float, volume: int) -> None:
        """开做多价差（买near卖far）"""
        try:
            # 获取合约信息
            near_contract = self.gateway.symbol_contract_map_tqsdk.get("ag2604")
            far_contract = self.gateway.symbol_contract_map_tqsdk.get("ag2606")

            if not near_contract or not far_contract:
                self.gateway.write_log("未找到合约信息")
                return

            # 买入near（开仓）
            req_near = OrderRequest(
                symbol="ag2604",
                exchange=near_contract.exchange,
                direction=Direction.LONG,
                type=OrderType.MARKET,
                volume=volume,
                price=0,
                offset=Offset.OPEN,
                reference="spread_long_near"
            )
            order_id_near = self.gateway.send_order(req_near)

            # 卖出far（开仓）
            req_far = OrderRequest(
                symbol="ag2606",
                exchange=far_contract.exchange,
                direction=Direction.SHORT,
                type=OrderType.MARKET,
                volume=volume,
                price=0,
                offset=Offset.OPEN,
                reference="spread_long_far"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": "ag2604",
                    "direction": "LONG",
                    "offset": "OPEN",
                    "create_time": create_time,
                    "pair_order_id": order_id_far
                }
                self.pending_orders[order_id_far] = {
                    "symbol": "ag2606",
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
                    self.gateway.cancel_order(
                        CancelRequest(orderid=order_id_near.split('.')[-1],
                                     symbol="ag2604",
                                     exchange=near_contract.exchange)
                    )
                if order_id_far:
                    self.gateway.cancel_order(
                        CancelRequest(orderid=order_id_far.split('.')[-1],
                                     symbol="ag2606",
                                     exchange=far_contract.exchange)
                    )
                self.spread_position.clear()

        except Exception as e:
            self.gateway.write_log(f"做多价差开仓异常: {str(e)}")

    def _close_spread_position(self, force: bool = False) -> None:
        """平仓价差持仓"""
        try:
            position_type = self._get_current_position_type()
            if not position_type:
                return

            if position_type == "short_spread":
                # 平做空价差：买near卖far
                self._send_close_orders("ag2604", Direction.LONG, "ag2606", Direction.SHORT)
            elif position_type == "long_spread":
                # 平做多价差：卖near买far
                self._send_close_orders("ag2604", Direction.SHORT, "ag2606", Direction.LONG)

            # 清空持仓
            self.spread_position.clear()
            self.gateway.write_log("价差持仓已平仓")

        except Exception as e:
            self.gateway.write_log(f"平仓异常: {str(e)}")

    def _send_close_orders(self, near_symbol: str, near_direction: Direction,
                          far_symbol: str, far_direction: Direction) -> None:
        """发送平仓订单"""
        try:
            near_contract = self.gateway.symbol_contract_map_tqsdk.get(near_symbol)
            far_contract = self.gateway.symbol_contract_map_tqsdk.get(far_symbol)

            if not near_contract or not far_contract:
                self.gateway.write_log("未找到合约信息")
                return

            # near平仓
            req_near = OrderRequest(
                symbol=near_symbol,
                exchange=near_contract.exchange,
                direction=near_direction,
                type=OrderType.MARKET,
                volume=1,
                price=0,
                offset=Offset.CLOSETODAY,
                reference="spread_close_near"
            )
            order_id_near = self.gateway.send_order(req_near)

            # far平仓
            req_far = OrderRequest(
                symbol=far_symbol,
                exchange=far_contract.exchange,
                direction=far_direction,
                type=OrderType.MARKET,
                volume=1,
                price=0,
                offset=Offset.CLOSETODAY,
                reference="spread_close_far"
            )
            order_id_far = self.gateway.send_order(req_far)

            if order_id_near and order_id_far:
                # 记录待成交订单
                create_time = datetime.now().timestamp()
                self.pending_orders[order_id_near] = {
                    "symbol": near_symbol,
                    "direction": near_direction.value,
                    "offset": "CLOSETODAY",
                    "create_time": create_time,
                    "pair_order_id": order_id_far
                }
                self.pending_orders[order_id_far] = {
                    "symbol": far_symbol,
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

    def _check_pending_orders_timeout(self, timeout: float) -> None:
        """检查待成交订单超时"""
        try:
            current_time = datetime.now().timestamp()
            timeout_orders = []

            for order_id, order_info in self.pending_orders.items():
                if current_time - order_info["create_time"] > timeout:
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
        """撤销订单"""
        try:
            # vt_orderid格式: GATEWAYNAME.orderid
            parts = vt_orderid.split('.')
            orderid = parts[-1] if len(parts) > 1 else vt_orderid

            # 从pending_orders中获取订单信息
            if vt_orderid in self.pending_orders:
                symbol = self.pending_orders[vt_orderid]["symbol"]
                contract = self.gateway.symbol_contract_map_tqsdk.get(symbol)
                if contract:
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

    def _handle_partial_fill(self) -> None:
        """处理单腿成交情况"""
        try:
            position_type = self._get_current_position_type()
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
        """紧急平仓单腿"""
        try:
            if position_type == "short_spread":
                if leg == "near":
                    # 平near空头
                    self._send_emergency_close_order("ag2604", Direction.LONG)
                else:
                    # 平far多头
                    self._send_emergency_close_order("ag2606", Direction.SHORT)
            elif position_type == "long_spread":
                if leg == "near":
                    # 平near多头
                    self._send_emergency_close_order("ag2604", Direction.SHORT)
                else:
                    # 平far空头
                    self._send_emergency_close_order("ag2606", Direction.LONG)

            # 清空持仓
            self.spread_position.clear()

        except Exception as e:
            self.gateway.write_log(f"紧急平仓异常: {str(e)}")

    def _send_emergency_close_order(self, symbol: str, direction: Direction) -> None:
        """发送紧急平仓订单"""
        try:
            contract = self.gateway.symbol_contract_map_tqsdk.get(symbol)
            if not contract:
                return

            req = OrderRequest(
                symbol=symbol,
                exchange=contract.exchange,
                direction=direction,
                type=OrderType.MARKET,
                volume=1,
                price=0,
                offset=Offset.CLOSETODAY,
                reference="emergency_close"
            )
            order_id = self.gateway.send_order(req)
            self.gateway.write_log(f"紧急平仓订单已发送: {symbol} {direction.value} {order_id}")

        except Exception as e:
            self.gateway.write_log(f"发送紧急平仓订单异常: {str(e)}")

    def on_order_status_update(self, vt_orderid: str, status: Status) -> None:
        """订单状态更新回调（从TdApi调用）"""
        try:
            # 更新订单状态映射
            self.order_status_map[vt_orderid] = status

            # 检查是否是待成交订单
            if vt_orderid in self.pending_orders:
                order_info = self.pending_orders[vt_orderid]
                pair_order_id = order_info.get("pair_order_id")

                # 订单完全成交
                if status == Status.ALLTRADED:
                    self.gateway.write_log(f"订单完全成交: {vt_orderid}")

                    # 更新持仓状态
                    position_type = self._get_current_position_type()
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
                    if pair_order_id in self.pending_orders:
                        # 配对订单还在待成交，检查其状态
                        self._handle_partial_fill()
                    else:
                        # 配对订单已不在待成交列表，可能已成交
                        self._handle_partial_fill()

        except Exception as e:
            self.gateway.write_log(f"订单状态更新异常: {str(e)}")

    def close(self) -> None:
        """关闭连接"""
        self.active = False

        # 等待线程结束
        if self.thread and self.thread.is_alive():
            # 为了触发await_update解除阻塞，所以订阅了一个行情连接
            _ = self.api.get_quote('ag2606')
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

    # ==================== 测试相关方法 ====================

    def _order_ag2604(self, symbol: str, quote: Quote) -> None:
        """测试ag2604下单功能"""
        # 只在ag2604上操作，且只下单一次
        if symbol != "ag2604" or self.order_placed:
            return

        # 标记已下单，避免重复下单
        self.order_placed = True

        try:
            # 获取合约信息
            contract: ContractData | None = self.gateway.symbol_contract_map_tqsdk.get(symbol, None)
            if not contract:
                self.gateway.write_log(f"未找到合约{symbol}的合约信息")
                return

            # 获取当前价格
            current_price = quote.last_price
            if current_price <= 0:
                self.gateway.write_log(f"获取{symbol}当前价格失败")
                return

            self.gateway.write_log(f"开始测试下单 {symbol}，当前价格: {current_price}")

            # 下单1：买单（开仓）
            req_buy: OrderRequest = OrderRequest(
                symbol=symbol,
                exchange=contract.exchange,
                direction=Direction.LONG,
                type=OrderType.MARKET,
                volume=1,
                price=0,  # 市价单价格设为0
                offset=Offset.OPEN,
                reference="test_buy"
            )
            order_id_buy: str = self.gateway.send_order(req_buy)

            if order_id_buy:
                self.order_ids.append(order_id_buy)
                self.gateway.write_log(f"买单已发送: {order_id_buy}")
            else:
                self.gateway.write_log("买单发送失败")
                return

            # 下单2：卖单（开仓）
            req_sell: OrderRequest = OrderRequest(
                symbol=symbol,
                exchange=contract.exchange,
                direction=Direction.SHORT,
                type=OrderType.MARKET,
                volume=1,
                price=0,  # 市价单价格设为0
                offset=Offset.OPEN,
                reference="test_sell"
            )
            order_id_sell: str = self.gateway.send_order(req_sell)

            if order_id_sell:
                self.order_ids.append(order_id_sell)
                self.gateway.write_log(f"卖单已发送: {order_id_sell}")
            else:
                self.gateway.write_log("卖单发送失败")
                return

            # 启动定时器，10秒后平仓
            timer = Thread(target=self._delay_close_positions, args=(symbol, contract,))
            timer.daemon = True
            timer.start()
            self.gateway.write_log("已启动10秒定时器，届时将自动平仓")

        except Exception as e:
            self.gateway.write_log(f"下单异常: {str(e)}")

    def _delay_close_positions(self, symbol: str, contract: ContractData) -> None:
        """延迟平仓的线程函数"""
        sleep(10)  # 等待10秒
        self.gateway.write_log("10秒已到，开始平仓...")
        self._close_ag2604_positions(symbol, contract)

    def _close_ag2604_positions(self, symbol: str, contract: ContractData) -> None:
        """智能平仓ag2604的持仓，区分上期所今仓昨仓"""
        try:
            # 先查询持仓，获取持仓详情
            self.gateway.query_position()

            # 这里有update_pos_condition同步问题，所以加锁
            with self.gateway.update_pos_condition:
                self.gateway.update_pos_condition.wait()

                # 获取多单持仓（Direction.LONG）
                long_positions = [pos for pos in self.gateway.positions_for_tqsdk.values()
                                  if pos.symbol == symbol and pos.direction == Direction.LONG and pos.volume > 0]

                # 获取空单持仓（Direction.SHORT）
                short_positions = [pos for pos in self.gateway.positions_for_tqsdk.values()
                                   if pos.symbol == symbol and pos.direction == Direction.SHORT and pos.volume > 0]
                # 临时变量，用完即刻清空
                self.gateway.positions_for_tqsdk.clear()

            # 平多单
            for pos in long_positions:
                close_volume = pos.volume
                if close_volume <= 0:
                    continue

                # 判断平仓offset：上期所需要区分今昨仓
                if contract.exchange in [Exchange.SHFE, Exchange.INE]:
                    # 如果有昨仓，先平昨仓；剩下的平今仓
                    if pos.yd_volume > 0:
                        close_yd_volume = min(pos.yd_volume, close_volume)
                        self._send_close_order(symbol, contract, Direction.SHORT,
                                              close_yd_volume, Offset.CLOSEYESTERDAY)
                        close_volume -= close_yd_volume

                    if close_volume > 0:
                        self._send_close_order(symbol, contract, Direction.SHORT,
                                              close_volume, Offset.CLOSETODAY)
                else:
                    # 非上期所，直接平仓
                    self._send_close_order(symbol, contract, Direction.SHORT,
                                          close_volume, Offset.CLOSE)

            # 平空单
            for pos in short_positions:
                close_volume = pos.volume
                if close_volume <= 0:
                    continue

                # 判断平仓offset：上期所需要区分今昨仓
                if contract.exchange in [Exchange.SHFE, Exchange.INE]:
                    # 如果有昨仓，先平昨仓；剩下的平今仓
                    if pos.yd_volume > 0:
                        close_yd_volume = min(pos.yd_volume, close_volume)
                        self._send_close_order(symbol, contract, Direction.LONG,
                                              close_yd_volume, Offset.CLOSEYESTERDAY)
                        close_volume -= close_yd_volume

                    if close_volume > 0:
                        self._send_close_order(symbol, contract, Direction.LONG,
                                              close_volume, Offset.CLOSETODAY)
                else:
                    # 非上期所，直接平仓
                    self._send_close_order(symbol, contract, Direction.LONG,
                                          close_volume, Offset.CLOSE)

            # 清空订单记录
            self.order_ids.clear()

        except Exception as e:
            self.gateway.write_log(f"平仓异常: {str(e)}")

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
