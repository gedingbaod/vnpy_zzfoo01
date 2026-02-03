"""
风险管理模块
包含收盘时间检查、强制平仓等风险控制功能
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, time
from threading import Thread
from typing import TYPE_CHECKING

from tqsdk.objs import Quote

from common.vnpy_time import split_cross_day_time

if TYPE_CHECKING:
    from common.strategy_spread import SpreadTradingStrategy, BaseStrategy

# 收盘前多少分钟停止交易
CLOSE_BEFORE_MINUTE = 15


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
        strategy : SpreadTradingStrategy
            策略实例，用于访问策略状态、持仓、交易功能等
        """
        if not hasattr(self, "strategy"):
            self.strategy = strategy
            self.thread: Thread = None  # 风险管理线程
            self.active: bool = True  # 风险管理线程是否激活
            # 用于打断wait()等待，使线程能快速退出
            self.interrupt_event = threading.Event()

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
                self.interrupt_event.wait(180)

                # 临时使用近月合约，后期可以再优化
                self._check_closing_time(self.strategy.near_symbol)

            except Exception as e:
                if self.active:
                    self.strategy.gateway.write_log(f"风险检查异常: {str(e)}")
                # break

        print("风险管理线程关闭")

    def _check_closing_time(self, symbol: str) -> None:
        # 获取near合约的行情
        check_quote = self.strategy.gateway.tq_md_api.quotes.get(symbol, None)
        if not check_quote or check_quote.datetime == '':
            return

        # 检查是否进入收盘时间
        if self._is_closing_time(check_quote):
            if not self.strategy.is_closing_time:
                # 为了提升性能，这里不加锁了，所以执行两次，避免同步问题出现
                self.strategy.is_closing_time = True
                self.strategy.is_closing_time = True
                self.strategy.gateway.write_log(f"进入收盘前{CLOSE_BEFORE_MINUTE}分钟，停止新开仓")

            # 强制平仓
            if self.strategy.global_position:
                self.strategy.gateway.write_log("收盘前强制平仓")
                self.strategy.close_position(force=True)
        else:
            # 已经不在收盘时间，重置标志
            if self.strategy.is_closing_time:
                self.strategy.gateway.write_log("已过收盘时间，恢复交易")
                # 为了提升性能，这里不加锁了，所以执行两次，避免同步问题
                self.strategy.is_closing_time = False
                self.strategy.is_closing_time = False

    def _is_closing_time(self, quote: Quote) -> bool:
        """
        判断是否处于收盘前15分钟

        Parameters
        ----------
        quote : Quote
            行情数据

        Returns
        -------
        bool
            是否处于收盘前15分钟
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

                if end_str == '23:59:59' and len(periods) == 2:
                    # 夜盘的隔夜逻辑，在夜盘第一段，可以跳过'23:59:59'
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
