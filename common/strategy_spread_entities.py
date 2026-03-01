from common.strategy_spread import SpreadTradingStrategy
from vnpy.trader.constant import Exchange


class AgSpreadStrategy(SpreadTradingStrategy):
    """
    白银跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        super().__init__(gateway, near_symbol, far_symbol, Exchange.SHFE)
        # "ag2604", "ag2606"
        #     "spread.near_symbol": "ag2604",
        #     "spread.far_symbol": "ag2606",
        # 设置白银特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.min_profit_points = 3    # 基本利润（最小盈利）
        self.slippage_points = 3 * 4   # 做一次差价就是4次下单，一次滑点设为3
        self.commission_point = 16     # 做一次差价开平的手续费成本,240，一跳15元
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期
        self.price_tick_min = 1        # 一跳的最小变动价格

class NiSpreadStrategy(SpreadTradingStrategy):
    """
    镍跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        super().__init__(gateway, near_symbol, far_symbol, Exchange.SHFE)
        #     "spread.near_symbol": "ni2603",
        #     "spread.far_symbol": "ni2605",
        # 设置镍特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期
        self.price_tick_min = 10       # 一跳的最小变动价格
        self.min_profit_points = 10    # 基本利润（最小盈利）
        self.slippage_points = 3 * 4   # 做一次差价就是4次下单，一次滑点设为3
        self.commission_point = 2      # 做一次差价开平的手续费成本,12，一跳10元

class SnSpreadStrategy(SpreadTradingStrategy):
    """
    锡跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        super().__init__(gateway, near_symbol, far_symbol, Exchange.SHFE)
        #     "spread.near_symbol": "sn2603",
        #     "spread.far_symbol": "sn2604",
        # 设置锡特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.min_profit_points = 3    # 基本利润（最小盈利）
        self.slippage_points = 50 * 4  # 做一次差价就是4次下单，一次滑点设为50
        self.commission_point = 2      # 做一次差价开平的手续费成本,12，一跳10元
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期
        self.price_tick_min = 10       # 一跳的最小变动价格
