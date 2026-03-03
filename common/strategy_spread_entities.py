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
        self.min_profit_points = 10    # 基本利润（最小盈利）
        self.slippage_points = 40      # 做一次差价就是4次下单，会损失40个点
        self.commission_point = 16     # 做一次差价开平的手续费成本,240，一跳15元
        self.price_tick_min = 1        # 一跳的最小变动价格
        self.open_delay_sec = 15       # 开仓状态延时秒数设置，减少行情波动影响
        # K线计算规则
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期

class NiSpreadStrategy(SpreadTradingStrategy):
    """
    镍跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        super().__init__(gateway, near_symbol, far_symbol, Exchange.SHFE)
        #     "spread.near_symbol": "ni2603",
        #     "spread.far_symbol": "ni2605",
        # 设置镍特定的策略参数   静态成本10+10+80=100
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.price_tick_min = 10       # 一跳的最小变动价格
        self.min_profit_points = 50    # 基本利润（最小盈利）
        self.slippage_points = 20 * 4  # 做一次差价就是4次下单，一次滑点设为3
        self.commission_point = 10     # 做一次差价开平的手续费成本,12，一跳10元
        self.open_delay_sec = 15       # 开仓状态延时秒数设置，减少行情波动影响
        # K线计算规则
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期

class SnSpreadStrategy(SpreadTradingStrategy):
    """
    锡跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        super().__init__(gateway, near_symbol, far_symbol, Exchange.SHFE)
        #     "spread.near_symbol": "sn2603",
        #     "spread.far_symbol": "sn2604",
        # 设置特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.8       # 订单超时时间（秒）
        self.min_profit_points = 3     # 基本利润（最小盈利）
        self.slippage_points = 50 * 4  # 做一次差价就是4次下单，一次滑点设为50
        self.commission_point = 2      # 做一次差价开平的手续费成本,12，一跳10元
        self.price_tick_min = 10       # 一跳的最小变动价格
        self.open_delay_sec = 15       # 开仓状态延时秒数设置，减少行情波动影响
        # K线计算规则
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期

class BzSpreadStrategy(SpreadTradingStrategy):
    """
    纯苯跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        super().__init__(gateway, near_symbol, far_symbol, Exchange.DCE)
        #     "spread.near_symbol": "bz2604",
        #     "spread.far_symbol": "bz2605",
        # 设置锡特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.9       # 订单超时时间（秒）
        self.slippage_points = 30      # 做一次差价就是4次下单，一次滑点设为30
        self.commission_point = 3      # 做一次差价开平的手续费成本80元，一跳30元
        self.price_tick_min = 1        # 一跳的最小变动价格，一手30元
        self.min_profit_points = 10    # 基本利润（最小盈利）
        self.open_delay_sec = 15       # 开仓状态延时秒数设置，减少行情波动影响

        # K线计算规则
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期

class SiSpreadStrategy(SpreadTradingStrategy):
    """
    工业硅跨期套利策略
    """
    def __init__(self, gateway: "BaseGatewayTq", near_symbol: str, far_symbol: str):
        super().__init__(gateway, near_symbol, far_symbol, Exchange.GFEX)
        #     "spread.near_symbol": "si2604",
        #     "spread.far_symbol": "si2605",
        # 设置特定的策略参数
        self.transaction_volume = 1    # 交易手数
        self.order_timeout = 0.9       # 订单超时时间（秒）
        self.min_profit_points = 10    # 基本利润（最小盈利）合约点数，10个点就是50元
        self.slippage_points = 10 * 4  # 做一次差价就是4次下单，一次滑点设为10
        self.commission_point = 5      # 做一次差价开平的手续费成本5*4元，换算成点数，一跳5个点25元
        self.price_tick_min = 5        # 一跳的最小变动价格
        self.open_delay_sec = 15       # 开仓状态延时秒数设置，减少行情波动影响

        # K线计算规则
        self.klines_std_k = 3          # 计算标准差倍数，用于计算上下轨
        self.klines_windows = 20       # kline的计算窗口，请求时会请求双倍数据
        self.klines_duration = 15 * 60 # kline的请求周期
