# 跨期套利策略模块化说明

## 重构概述

将跨期套利策略从 `TqSdkMdApi` 类中提取出来，创建独立的策略模块 `strategy_spread.py`，实现策略与行情API的分离。

## 重构内容

### 1. 创建新文件
**文件路径**：`common/strategy_spread.py`

**主要类**：
- `SpreadTradingStrategy` - 跨期套利策略基类
- `AgSpreadStrategy` - 白银跨期套利策略实现（ag2604/ag2606）

### 2. 修改文件
**文件路径**：`common/tqsdk_gateway.py`

**修改内容**：
1. 导入策略模块：`from common.strategy_spread import AgSpreadStrategy`
2. 在 `__init__` 中创建策略实例：`self.spread_strategy = AgSpreadStrategy(gateway)`
3. 在 `_run` 中调用策略：`self.spread_strategy.on_tick(near_quote, far_quote)`
4. 在 `on_order_status_update` 中转发：`self.spread_strategy.on_order_status_update(vt_orderid, status)`
5. 删除所有策略相关的方法（已移至策略类）

### 3. 代码行数减少
- **删除代码**：约540行（策略方法从TqSdkMdApi中删除）
- **新增代码**：约620行（strategy_spread.py新策略类）
- **净增加**：约80行（由于模块化带来的接口和文档代码）

## 架构优势

### 1. 职责分离
- **TqSdkMdApi**：专注于行情接入和数据转换
- **SpreadTradingStrategy**：专注于交易策略逻辑
- **AgSpreadStrategy**：专注于具体策略实现

### 2. 代码复用
- 策略可以在不同的行情源中复用（TQSDK、TTS等）
- 策略可以在不同的交易接口中复用（CTP、CTPTEST等）
- 基类 `SpreadTradingStrategy` 可以被其他套利策略继承

### 3. 易于维护
- 策略逻辑集中在策略类中，便于修改和测试
- 行情API专注于数据获取，职责清晰
- 新增策略只需继承基类，无需修改行情API

### 4. 易于扩展
- 可以添加新的策略类（如铜跨期、股指跨期等）
- 可以在同一行情API中运行多个策略
- 策略参数可以独立配置

## 策略类结构

### SpreadTradingStrategy 基类

**主要职责**：
- 管理持仓状态
- 处理订单状态更新
- 执行风控检查
- 执行开仓/平仓逻辑

**主要方法**：
```python
def on_tick(self, near_quote: Quote, far_quote: Quote) -> None
    """行情更新回调，主策略逻辑"""

def open_short_spread(self, near_quote, far_quote, spread) -> None
    """开做空价差"""

def open_long_spread(self, near_quote, far_quote, spread) -> None
    """开做多价差"""

def close_position(self, force=False) -> None
    """平仓"""

def on_order_status_update(self, vt_orderid: str, status: Status) -> None
    """订单状态更新回调"""
```

**策略参数**：
- `upper_band` - 上轨阈值
- `middle_band` - 中轨阈值
- `lower_band` - 下轨阈值
- `transaction_volume` - 交易手数
- `order_timeout` - 订单超时时间
- `max_spread_cost` - 最大买卖价差成本
- `stop_loss_points` - 止损点数

### AgSpreadStrategy 子类

**继承**：`SpreadTradingStrategy`

**特定配置**：
- 合约对：ag2604（近月）/ ag2606（远月）
- 参数值：upper_band=100, middle_band=35, lower_band=-30

## 使用方式

### 在 TqSdkMdApi 中使用

```python
from common.tqsdk_gateway import TqSdkMdApi

class TtstqGateway(BaseGateway):
    def __init__(self, event_engine, gateway_name):
        super().__init__(event_engine, gateway_name)

        # 创建TQSDK行情API（内部已包含策略）
        self.tq_md_api = TqSdkMdApi(self)

    def connect(self, setting):
        # ... 连接逻辑
        if market_source == "TQSDK":
            self.tq_md_api.connect()
```

### 直接使用策略类

```python
from common.strategy_spread import AgSpreadStrategy
from common.gateway_tq import BaseGatewayTq

# 创建策略实例
gateway = BaseGatewayTq(...)
strategy = AgSpreadStrategy(gateway)

# 在行情回调中使用
def on_market_data(near_quote, far_quote):
    strategy.on_tick(near_quote, far_quote)

# 在订单回调中使用
def on_order_update(vt_orderid, status):
    strategy.on_order_status_update(vt_orderid, status)
```

### 创建新的套利策略

```python
from common.strategy_spread import SpreadTradingStrategy

class CuSpreadStrategy(SpreadTradingStrategy):
    """铜跨期套利策略"""

    def __init__(self, gateway):
        # cu2503 (近月) / cu2505 (远月)
        super().__init__(gateway, "cu2503", "cu2505")

        # 设置铜特定的策略参数
        self.upper_band = 500
        self.middle_band = 200
        self.lower_band = -200
        self.transaction_volume = 2
```

## 数据流

### 行情数据流
```
TQSDK -> TqSdkMdApi._run() -> TqSdkMdApi._process_tick() -> Gateway.on_tick()
                                              -> AgSpreadStrategy.on_tick()
```

### 订单数据流
```
TdApi.onRtnOrder() -> TqSdkMdApi.on_order_status_update() -> AgSpreadStrategy.on_order_status_update()
```

### 交易指令流
```
AgSpreadStrategy -> Gateway.send_order() -> TdApi.send_order() -> 交易所
```

## 风控机制

策略类中包含完整的风控机制：

1. **数据同步检查** - 确保两个合约行情时间同步
2. **收盘时间检查** - 收盘前5分钟强制平仓
3. **买卖价差检查** - 滑点成本超过阈值停止交易
4. **涨跌停检查** - 接近涨跌停停止交易
5. **订单超时处理** - 0.8秒未成交自动撤销
6. **单腿成交处理** - 立即平仓已成交的腿
7. **止损逻辑** - 固定50点止损

## 迁移指南

### 旧代码（在TqSdkMdApi中）
```python
class TqSdkMdApi:
    def __init__(self, gateway):
        self.spread_position = {}
        self.pending_orders = {}
        # ... 策略状态

    def _spread_ag2604_ag2606(self, near_quote, far_quote):
        # 策略逻辑
        pass
```

### 新代码（策略独立）
```python
# 策略类
class AgSpreadStrategy(SpreadTradingStrategy):
    def __init__(self, gateway):
        super().__init__(gateway, "ag2604", "ag2606")
        # 策略参数已配置

    def on_tick(self, near_quote, far_quote):
        # 策略逻辑
        pass

# 行情API类
class TqSdkMdApi:
    def __init__(self, gateway):
        self.spread_strategy = AgSpreadStrategy(gateway)

    def _run(self):
        # 调用策略
        self.spread_strategy.on_tick(near_quote, far_quote)
```

## 依赖关系

### strategy_spread.py 依赖
- `vnpy.trader.constant` - 交易常量
- `vnpy.trader.object` - 数据对象
- `common.gateway_tq` - Gateway基类

### tqsdk_gateway.py 依赖
- `common.strategy_spread` - 策略模块（新增）
- 其他原有依赖保持不变

## 文件清单

### 新增文件
- `common/strategy_spread.py` - 跨期套利策略模块

### 修改文件
- `common/tqsdk_gateway.py`：
  - 删除策略相关方法（约540行）
  - 添加策略实例化和调用

### 相关文档
- `docs/ag2604_ag2606_spread_trading_strategy.md` - 策略详细文档
- `docs/tqsdk_gateway_refactoring.md` - TQSDK重构文档

## 后续优化

### 1. 策略配置文件
创建策略配置文件，避免硬编码：
```python
# strategy_config.yaml
ag_spread:
  near_symbol: "ag2604"
  far_symbol: "ag2606"
  upper_band: 100
  middle_band: 35
  lower_band: -30
```

### 2. 策略工厂
创建策略工厂，根据配置动态创建策略：
```python
class StrategyFactory:
    @staticmethod
    def create_strategy(strategy_type: str, gateway):
        if strategy_type == "ag_spread":
            return AgSpreadStrategy(gateway)
        elif strategy_type == "cu_spread":
            return CuSpreadStrategy(gateway)
```

### 3. 策略管理器
创建策略管理器，管理多个策略实例：
```python
class StrategyManager:
    def __init__(self, gateway):
        self.strategies = []

    def add_strategy(self, strategy):
        self.strategies.append(strategy)

    def on_tick(self, symbol, quote):
        for strategy in self.strategies:
            strategy.on_tick(quote)
```

### 4. 性能统计
添加策略绩效统计功能：
- 总盈利/亏损
- 交易次数
- 胜率
- 最大回撤
- 平均持仓时间

## 测试建议

### 单元测试
```python
def test_strategy_open_short():
    gateway = MockGateway()
    strategy = AgSpreadStrategy(gateway)

    near_quote = create_quote("ag2604", bid_price1=8000, ask_price1=8005)
    far_quote = create_quote("ag2606", bid_price1=7950, ask_price1=7955)

    # 触发开仓
    near_quote.bid_price1 = 8100  # 价差 > 100
    strategy.on_tick(near_quote, far_quote)

    assert "short_spread" in strategy.spread_position
```

### 集成测试
- 在模拟环境中测试完整交易流程
- 验证订单状态回调
- 验证风控机制

## 注意事项

1. **线程安全**：策略在行情线程中执行，注意线程安全
2. **异常处理**：策略中的异常会被捕获并记录日志
3. **订单跟踪**：策略维护自己的订单状态，不要依赖外部状态
4. **参数配置**：策略参数应该在初始化时配置，运行时不要修改

## 版本历史

- **v1.0** (2025-01-27)：初始版本，将策略从TqSdkMdApi中提取为独立模块
