import numpy as np

from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager,
)
from vnpy.trader.constant import Interval


class DoubleMaHourStrategy(CtaTemplate):
    """1小时级别双均线策略（基于示例 DoubleMaStrategy 改造）"""

    author = "gedingbaod"

    # 均线参数：界面上可调（parameters 列表决定）
    fast_window: int = 10
    slow_window: int = 20

    # 策略变量：界面监控面板可见
    fast_ma0: float = 0.0
    fast_ma1: float = 0.0
    slow_ma0: float = 0.0
    slow_ma1: float = 0.0

    parameters = ["fast_window", "slow_window"]
    variables = ["fast_ma0", "fast_ma1", "slow_ma0", "slow_ma1"]

    def on_init(self) -> None:
        """策略初始化：创建1分钟->1小时的K线合成器"""
        self.write_log("DoubleMaHourStrategy策略初始化")

        # 核心改动：window=1 + interval=HOUR，把1分钟bar合成为1小时bar
        # on_bar 收到的是1分钟bar（仅作推进，不写逻辑）
        # 合成完成的1小时bar会回调 on_window_bar，即下面的 on_hour_bar
        self.bg: BarGenerator = BarGenerator(
            self.on_bar,
            window=1,
            on_window_bar=self.on_hour_bar,
            interval=Interval.HOUR,
        )
        self.am: ArrayManager = ArrayManager()

        # 回补历史数据：小时bar一天只有6~9根，ArrayManager需要100根才inited，
        # 10天不够，必须加大到20天以上
        self.load_bar(20)

    def on_start(self) -> None:
        """策略启动"""
        self.write_log("策略启动")
        self.put_event()

    def on_stop(self) -> None:
        """策略停止"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData) -> None:
        """tick行情：推给合成器，由其合成1分钟bar再合成小时bar"""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """1分钟bar回调：喂给合成器，实时tick流和load_bar历史回补都经过这里"""
        # 必须推进合成器，否则小时bar永远不会生成，ArrayManager无法inited
        self.bg.update_bar(bar)

    def on_hour_bar(self, bar: BarData) -> None:
        """1小时bar回调：原双均线交易逻辑放这里"""
        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        # 快慢均线（SMA），取最近两根判断金叉/死叉
        fast_ma: np.ndarray = am.sma(self.fast_window, array=True)
        self.fast_ma0 = fast_ma[-1]
        self.fast_ma1 = fast_ma[-2]

        slow_ma: np.ndarray = am.sma(self.slow_window, array=True)
        self.slow_ma0 = slow_ma[-1]
        self.slow_ma1 = slow_ma[-2]

        cross_over: bool = self.fast_ma0 > self.slow_ma0 and self.fast_ma1 < self.slow_ma1
        cross_below: bool = self.fast_ma0 < self.slow_ma0 and self.fast_ma1 > self.slow_ma1

        # 金叉：做多（空仓开多；有空单先平再反手）
        if cross_over:
            if self.pos == 0:
                self.buy(bar.close_price, 1)
            elif self.pos < 0:
                self.cover(bar.close_price, 1)
                self.buy(bar.close_price, 1)

        # 死叉：做空（空仓开空；有多单先平再反手）
        elif cross_below:
            if self.pos == 0:
                self.short(bar.close_price, 1)
            elif self.pos > 0:
                self.sell(bar.close_price, 1)
                self.short(bar.close_price, 1)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """委托回报"""
        pass

    def on_trade(self, trade: TradeData) -> None:
        """成交回报"""
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """本地停止单回报"""
        pass
