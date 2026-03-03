"""
风险管理模块
包含收盘时间检查、强制平仓等风险控制功能
"""

# from __future__ import annotations

import threading
from datetime import datetime, timedelta, time
from threading import Thread
from typing import TYPE_CHECKING

from tqsdk.objs import Quote

from common.vnpy_time import split_cross_day_time

from vnpy.event import Event

from common.strategy_spread import BaseStrategy, SPREAD_POSITION, SPREAD_POSITION_TYPE, SPREAD_POSITION_TYPE_SHORT, \
    SPREAD_POSITION_TYPE_LONG

# 导入 MongoDB 保存功能
try:
    from mongodb.save_position import save_positions_to_mongodb
    MONGODB_SAVE_AVAILABLE = True
except ImportError:
    MONGODB_SAVE_AVAILABLE = False
    print("警告：无法导入 MongoDB 保存模块，持仓历史将不会保存到数据库")

# 收盘前多少分钟停止交易
CLOSE_BEFORE_MINUTE = 5
EXCHANGE_TRADE_TIME: dict[str, dict] = {
    "ag": {"day": [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]], "night": [["21:00:00", "26:30:00"]]},
    "ni": {"day": [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]], "night": [["21:00:00", "25:00:00"]]},
    "sn": {"day": [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]], "night": [["21:00:00", "25:00:00"]]},
    "bz": {"day": [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]], "night": [["21:00:00", "23:00:00"]]},
    'si': {"day": [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]]}
}

# 打印历史事件
EVENT_PRINT_HISTORY = 'ePrintHistory'

class RiskManager:
    """
    风险管理类
    主要是一个定时任务线程，在线程内做风控检查

    负责监控交易风险，包括：
    - 收盘时间检查
    - 强制平仓
    - 停止新开仓
    """

    # 这部分是class的属性
    _instance = None
    _lock = threading.Lock()  # 锁对象，保证线程安全

    # 因为类里包含线程，所以创建为单例模式
    def __new__(cls, *args, **kwargs):
        # 双重检查锁（DCL）：提升性能，仅第一次创建时加锁
        if cls._instance is None:
            with cls._lock:  # 加锁，防止多线程同时创建
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, strategy: "BaseStrategy"):
        """
        构造函数

        Parameters
        ----------
        strategy : BaseStrategy
            策略实例，用于访问策略状态、持仓、交易功能等
        """
        if not hasattr(self, "strategy"):
            self.strategy = strategy
            self.thread: Thread = None  # 风险管理线程
            self.active: bool = True  # 风险管理线程是否激活
            # 用于打断wait()等待，使线程能快速退出
            self.interrupt_event = threading.Event()
            self.strategy.gateway.event_engine.register(EVENT_PRINT_HISTORY, self.callback_priprint_history_position)

    def start(self) -> None:
        """
        启动风险管理线程

        在独立线程中运行风险检查循环，每3分钟检查一次
        """
        if self.thread is not None and self.thread.is_alive():
            self.strategy.gateway.write_log("风险管理线程已在运行")
            return

        # 确保激活标志为 True
        self.active = True
        self.thread = Thread(target=self.run_risk_manager_loop, daemon=True, name="risk_manager_thread")
        self.thread.start()
        self.strategy.gateway.write_log("风险管理线程已启动")

    def stop(self) -> None:
        """
        停止风险管理线程

        设置停止标志，打断等待，并等待线程结束
        """
        if self.thread is None:
            return

        # 设置停止标志
        self.active = False
        # 打断interrupt_event.wait()等待
        self.interrupt_event.set()
        # 等待线程结束
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        self.strategy.gateway.write_log("风险管理线程已停止")

    def run_risk_manager_loop(self) -> None:
        """
        风险管理循环（在独立线程中运行，每3分钟检查一次）

        功能：
        - 检查是否进入收盘时间
        - 收盘前停止新开仓
        - 收盘前强制平仓
        """
        while self.active:
            try:
                # 每3分钟检查一次
                self.interrupt_event.wait(300)

                # 检查价格限制
                rt_price_limit = self._check_price_limit_rm02()

                # 临时使用近月合约，后期可以再优化
                rt_closing_time = self._check_closing_time_rm01()

                # 逻辑与，全为True，结果为True
                self.strategy.is_tradable = (not rt_closing_time) and (not rt_price_limit)

                # 如果不可交易，做一次强制平仓
                if not self.strategy.is_tradable:
                    self.strategy.gateway.write_log("收盘前强制平仓")
                    self.strategy.close_position(force=True)

                self.save_history_to_mongodb()

            except Exception as e:
                if self.active:
                    self.strategy.gateway.write_log(f"风险检查异常: {str(e)}")
                import traceback
                traceback.print_exc()

        print("风险管理线程关闭")

    def _check_closing_time_rm01(self) -> bool:
        """
        判断是否处于收盘前15分钟

        Returns
        -------
        bool
            是否处于收盘前15分钟
        """

        # 取合约前两个字符
        symbol_2c = self.strategy.get_symbols()[0][0:2]
        symbol_2c = symbol_2c.lower()
        symbol_trading_time = EXCHANGE_TRADE_TIME[symbol_2c]
        current_time = datetime.now().time()

        # 遍历所有交易时段（day和night）
        for period_type in ["day", "night"]:
            if period_type not in symbol_trading_time:
                continue

            periods = symbol_trading_time[period_type]
            second_end_hour = int(periods[0][1].split(':')[0])
            if second_end_hour > 23:
                # 如果隔夜就拆成两队
                periods = split_cross_day_time(periods)

            for period in periods:
                start_str = period[0]  # "09:00:00"
                end_str = period[1]    # "10:15:00"

                if end_str == '23:59:59' and len(periods) == 2:
                    # 夜盘的隔夜逻辑，在夜盘第一段，可以跳过'23:59:59'
                    continue
                if end_str == "10:15:00" or end_str == "11:30:00":
                    # 白盘上午和中午不暂停交易
                    continue

                start_time = time.fromisoformat(start_str)
                end_time = time.fromisoformat(end_str)

                # 判断是否在收盘前15分钟
                closing_time = (datetime.combine(datetime.today(), end_time) -
                              timedelta(minutes=CLOSE_BEFORE_MINUTE)).time()

                # 如果在收盘前15分钟到收盘时间之间
                if closing_time <= current_time <= end_time:
                    return True

        return False

    def _check_price_limit_rm02(self) -> bool:
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
            near_quote = self.strategy.spread_quotes[0]
            far_quote = self.strategy.spread_quotes[1]
            # 计算near合约的涨跌停幅度
            near_upper_move = (near_quote.upper_limit - near_quote.pre_settlement) * self.strategy.max_price_limit_ratio
            near_lower_move = (near_quote.pre_settlement - near_quote.lower_limit) * self.strategy.max_price_limit_ratio

            # 计算far合约的涨跌停幅度
            far_upper_move = (far_quote.upper_limit - far_quote.pre_settlement) * self.strategy.max_price_limit_ratio
            far_lower_move = (far_quote.pre_settlement - far_quote.lower_limit) * self.strategy.max_price_limit_ratio

            # 检查当前价格变动
            near_change = near_quote.last_price - near_quote.pre_settlement
            far_change = far_quote.last_price - far_quote.pre_settlement

            # 如果接近涨跌停，返回True
            if near_change >= near_upper_move or near_change <= -near_lower_move:
                self.strategy.gateway.write_log(f"{near_quote.instrument_name}接近涨跌停，停止交易")
                return True
            if far_change >= far_upper_move or far_change <= -far_lower_move:
                self.strategy.gateway.write_log(f"{far_quote.instrument_name}接近涨跌停，停止交易")
                return True

            return False
        except Exception as e:
            self.strategy.gateway.write_log(f"检查涨跌停异常: {str(e)}")
            return False

    def save_history_to_mongodb(self):
        # 批量保存到 MongoDB（如果可用）
        history = self.strategy.history_position
        if MONGODB_SAVE_AVAILABLE and history:
            try:
                save_result = save_positions_to_mongodb(list(history))
            except Exception as e:
                self.strategy.gateway.write_log(f"批量保存持仓记录到 MongoDB 失败: {str(e)}")

    def callback_priprint_history_position(self, event: Event):
        self.print_history_position()

    def print_history_position(self) -> None:
        """
        打印历史持仓记录

        将self.strategy.history_position的内容格式化输出到日志
        同时保存到 MongoDB（如果可用）
        """

        # 先打印当前持仓现状
        output_lines: list[str] = [f"\n========== 当前持仓快照 =========="]
        current_position = self.strategy.global_position[SPREAD_POSITION]
        if current_position is None:
            output_lines.append("    当前持仓记录为空")
        else:
            output_lines.extend(output_position(current_position, is_current=True))

        # 在打印历史持仓记录
        history = self.strategy.history_position
        output_lines.append(f"========== 历史持仓记录 (共 {len(history)} 条) ==========")
        if not history:
            output_lines.append("    历史持仓记录为空")
            output_lines.append("========== 历史持仓记录打印完成 ==========")
            self.strategy.gateway.write_log("\n".join(output_lines))
            return

        for idx, position in enumerate(history, 1):
            output_lines.append(f"---------- 记录 {idx} ----------")
            output_lines.extend(output_position(position))

        output_lines.append("========== 历史持仓记录打印完成 ==========")

        # 一次性输出
        self.strategy.gateway.write_log("\n".join(output_lines))

def output_position(position: dict, is_current = False) -> list[str]:

    output_lines: list[str] = []
    # 打印关键字段
    position_status = position.get("position_status", "N/A")
    position_type = position.get(SPREAD_POSITION_TYPE, "N/A")
    output_lines.append(f"  状态: {position_status}  类型: {position_type}")

    # 时间信息（一行4个）
    time_parts: list[str] = []
    if position.get("open_start_time"):
        time_parts.append(f"开仓开始时间: {position.get('open_start_time')}")
    if position.get("open_finish_time"):
        time_parts.append(f"开仓完成时间: {position.get('open_finish_time')}")
    if position.get("close_start_time"):
        time_parts.append(f"平仓开始时间: {position.get('close_start_time')}")
    if position.get("close_finish_time"):
        time_parts.append(f"平仓完成时间: {position.get('close_finish_time')}")
    if time_parts:
        output_lines.append(f"  {'  '.join(time_parts)}")

    # 异常相关
    if position.get("exception_time"):
        output_lines.append(f"  异常时间: {position.get('exception_time')}")

    # 订单ID（一行4个）
    order_id_parts: list[str] = []
    if position.get("near_open_order_id"):
        order_id_parts.append(f"近月开仓订单ID: {position.get('near_open_order_id')}")
    if position.get("far_open_order_id"):
        order_id_parts.append(f"远月开仓订单ID: {position.get('far_open_order_id')}\n")
    if position.get("near_close_order_id"):
        order_id_parts.append(f"近月平仓订单ID: {position.get('near_close_order_id')}")
    if position.get("far_close_order_id"):
        order_id_parts.append(f"远月平仓订单ID: {position.get('far_close_order_id')}")
    if order_id_parts:
        output_lines.append(f"  {'  '.join(order_id_parts)}")

    # 订单状态（一行4个）
    status_parts: list[str] = []
    if position.get("near_open_status"):
        status_parts.append(f"近月开仓状态: {position.get('near_open_status')}")
    if position.get("far_open_status"):
        status_parts.append(f"远月开仓状态: {position.get('far_open_status')}")
    if position.get("near_close_status"):
        status_parts.append(f"近月平仓状态: {position.get('near_close_status')}")
    if position.get("far_close_status"):
        status_parts.append(f"远月平仓状态: {position.get('far_close_status')}")
    if status_parts:
        output_lines.append(f"  {'  '.join(status_parts)}")

    # 服务器时间（一行4个）
    servertime_parts: list[str] = []
    if position.get("near_open_servertime"):
        servertime_parts.append(f"近月开仓服务器时间: {position.get('near_open_servertime')}")
    if position.get("far_open_servertime"):
        servertime_parts.append(f"远月开仓服务器时间: {position.get('far_open_servertime')}\n")
    if position.get("near_close_servertime"):
        servertime_parts.append(f"近月平仓服务器时间: {position.get('near_close_servertime')}")
    if position.get("far_close_servertime"):
        servertime_parts.append(f"远月平仓服务器时间: {position.get('far_close_servertime')}")
    if servertime_parts:
        output_lines.append(f"  {'  '.join(servertime_parts)}")

    # 合约信息
    symbols: list[str] = []
    if position.get("near_symbol"):
        symbols.append(f"近月合约: {position.get('near_symbol')}")
    if position.get("far_symbol"):
        symbols.append(f"远月合约: {position.get('far_symbol')}")
    if symbols:
        output_lines.append(f"  {'  '.join(symbols)}")

    # 手数信息（一行4个）
    volume_parts: list[str] = []
    if position.get("near_volume") is not None:
        volume_parts.append(f"近月开仓手数: {position.get('near_volume')}")
    if position.get("near_yd_volume") is not None:
        volume_parts.append(f"近月昨仓手数: {position.get('near_yd_volume')}")
    if position.get("far_volume") is not None:
        volume_parts.append(f"远月开仓手数: {position.get('far_volume')}")
    if position.get("far_yd_volume") is not None:
        volume_parts.append(f"远月昨仓手数: {position.get('far_yd_volume')}")
    if volume_parts:
        output_lines.append(f"  {'  '.join(volume_parts)}")

    # 开仓价格信息
    open_price_parts: list[str] = []
    if position.get("near_open_price1") is not None:
        open_price_parts.append(f"近月开仓发送价: {position.get('near_open_price1')}")
    if position.get("far_open_price1") is not None:
        open_price_parts.append(f"远月开仓发送价: {position.get('far_open_price1')}\n")
    if position.get("near_open_price") is not None:
        open_price_parts.append(f"近月开仓成交价: {position.get('near_open_price')}")
    if position.get("far_open_price") is not None:
        open_price_parts.append(f"远月开仓成交价: {position.get('far_open_price')}")
    if open_price_parts:
        output_lines.append(f"  {'  '.join(open_price_parts)}")

    # 开仓时的指标
    if position.get("open_spread_indicator") is not None:
        output_lines.append(f"  {'  '.join(str(position.get("open_spread_indicator")))}")

    # 平仓价格信息
    close_price_parts: list[str] = []
    if position.get("near_close_price1") is not None:
        close_price_parts.append(f"近月平仓发送价: {position.get('near_close_price1')}")
    if position.get("far_close_price1") is not None:
        close_price_parts.append(f"远月平仓发送价: {position.get('far_close_price1')}\n")
    if position.get("near_close_price") is not None:
        close_price_parts.append(f"近月平仓成交价: {position.get('near_close_price')}")
    if position.get("far_close_price") is not None:
        close_price_parts.append(f"远月平仓成交价: {position.get('far_close_price')}")
    if close_price_parts:
        output_lines.append(f"  {'  '.join(close_price_parts)}")

    # 平仓时的指标
    if position.get("close_spread_indicator") is not None:
        output_lines.append(f"  {'  '.join(str(position.get("close_spread_indicator")))}")

    # 价差信息
    if position.get("open_send_spread") is not None:
        output_lines.append(f"  开仓发送价差: {position.get('open_send_spread')}")
    if position.get("open_real_spread") is not None:
        output_lines.append(f"  实际开仓价差: {position.get('open_real_spread')}")
    if position.get("close_send_spread") is not None:
        output_lines.append(f"  平仓发送价差: {position.get('close_send_spread')}")
    if position.get("close_real_spread") is not None:
        output_lines.append(f"  实际平仓价差: {position.get('close_real_spread')}")

    if not is_current:
        if (position.get("open_real_spread") is not None) and (position.get("close_real_spread") is not None):
            if position.get(SPREAD_POSITION_TYPE) == SPREAD_POSITION_TYPE_SHORT:
                position["real_profit"] = position.get("open_real_spread") - position.get("close_real_spread")
            elif position.get(SPREAD_POSITION_TYPE) == SPREAD_POSITION_TYPE_LONG:
                position["real_profit"] = position.get("close_real_spread") - position.get("open_real_spread")
            else:
                position["real_profit"] = 0
            output_lines.append(f"  实际利润: {position.get('real_profit')}")
        pass
    return output_lines

if __name__ == "__main__":
    symbol_short = "ni"
    trading_time = EXCHANGE_TRADE_TIME[symbol_short]
    print(trading_time)