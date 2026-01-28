# TQSDK行情API重构说明

## 重构概述

将`TqSdkMdApi`类从`vnpy_ttstq`模块提取到`common`目录，使其可以在不同的gateway（vnpy_ctptq、vnpy_ctptesttq等）中复用。

## 重构内容

### 1. 创建新文件
**文件路径**：`common/tqsdk_gateway.py`

**主要功能**：
- TQSDK行情API通用类
- 白银跨期套利策略实现
- 测试下单功能
- 完整的订单管理和风控机制

**导出的类**：
- `TqSdkMdApi`：TQSDK行情API通用类

### 2. 修改原文件
**文件路径**：`vnpy_ttstq/vnpy_ttstq/gateway/ttstq_gateway.py`

**修改内容**：
- 添加导入：`from common.tqsdk_gateway import TqSdkMdApi`
- 删除原有的`TqSdkMdApi`类定义（第1010-2044行）
- 保留所有对`TqSdkMdApi`的引用

### 3. 其他gateway使用方式

#### vnpy_ctptq
```python
# 在vnpy_ctptq的gateway文件中
from common.tqsdk_gateway import TqSdkMdApi

class CtptqGateway(BaseGateway):
    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.tq_md_api: Union[TqSdkMdApi, None] = None  # TQSDK行情API
        # ... 其他代码

    def connect(self, setting: dict) -> None:
        # ... 连接交易接口

        # 根据配置选择行情源
        if self.market_source == "TQSDK":
            self.tq_md_api = TqSdkMdApi(self)
            self.tq_md_api.subscribed.update(self.md_api.subscribed)
            self.tq_md_api.connect()
```

#### vnpy_ctptesttq
```python
# 使用方式完全相同
from common.tqsdk_gateway import TqSdkMdApi

class CtptesttqGateway(BaseGateway):
    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.tq_md_api: Union[TqSdkMdApi, None] = None
```

## 依赖关系

### common/tqsdk_gateway.py依赖

```python
# vnpy核心模块
from vnpy.event.engine import EventEngine
from vnpy.trader.constant import Direction, Offset, Exchange, OrderType, Status
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import TickData, OrderRequest, CancelRequest, SubscribeRequest, ContractData
from vnpy.trader.utility import get_folder_path, ZoneInfo

# TQSDK模块
from tqsdk.objs import Quote
from tqsdk import TqApi, TqAuth

# 项目通用模块
from common.account.tq_account import tq_auth  # 可选
from common.vnpy_time import datetime_format  # 必需

# 全局变量（需要在主程序中定义）
symbol_contract_map: dict[str, ContractData] = {}
```

### 必需的全局变量

在使用`TqSdkMdApi`之前，需要定义以下全局变量：

```python
# 合约数据全局缓存字典
from vnpy.trader.object import ContractData
symbol_contract_map: dict[str, ContractData] = {}
```

**注意**：这个变量通常在gateway的`onRspQryInstrument`方法中被填充。

## TqSdkMdApi类的主要功能

### 1. 基础行情功能
- 连接TQSDK
- 订阅合约行情
- 处理行情数据并转换为vnpy格式
- 关闭连接

### 2. 跨期套利策略
- 白银跨期套利（ag2604/ag2606）
- 完整的开仓、平仓、止损逻辑
- 订单超时处理
- 单腿成交处理
- 涨跌停风控
- 收盘前强制平仓

### 3. 测试功能
- 测试下单功能（_order_ag2604）
- 自动平仓测试

### 4. 订单状态管理
- 订单状态回调（on_order_status_update）
- 待成交订单跟踪
- 持仓状态管理

## 重要方法

### 初始化和连接
```python
def __init__(self, gateway: BaseGateway, auth: TqAuth = None)
def connect(self) -> None
def subscribe(self, req: SubscribeRequest) -> None
def close(self) -> None
```

### 策略方法
```python
def _spread_ag2604_ag2606(self, near_quote: Quote, far_quote: Quote) -> None
def _open_short_spread(self, near_quote, far_quote, spread, volume)
def _open_long_spread(self, near_quote, far_quote, spread, volume)
def _close_spread_position(self, force=False)
```

### 订单管理
```python
def on_order_status_update(self, vt_orderid: str, status: Status) -> None
def _check_pending_orders_timeout(self, timeout: float) -> None
def _handle_partial_fill(self) -> None
```

### 风控方法
```python
def _is_closing_time(self, quote: Quote) -> bool
def _check_price_limit(self, near_quote, far_quote, ratio) -> bool
```

## 集成到TdApi

为了使订单状态回调正常工作，需要在TdApi的`onRtnOrder`方法中添加以下代码：

```python
def onRtnOrder(self, data: dict) -> None:
    """委托更新推送"""
    # ... 原有代码 ...

    self.gateway.on_order(order)

    # 通知TqSdkMdApi订单状态更新（用于套利策略）
    vt_orderid: str = f"{self.gateway_name}.{orderid}"
    if self.gateway.tq_md_api:
        self.gateway.tq_md_api.on_order_status_update(vt_orderid, order.status)

    self.sysid_orderid_map[data["OrderSysID"]] = orderid
```

## 测试

### 测试行情功能
```python
# 在vnpy_ttptq中使用
gateway = CtptqGateway(event_engine, "CTPTQ")
gateway.connect(setting)
gateway.subscribe(SubscribeRequest(symbol="ag2604", exchange=Exchange.SHFE))
```

### 测试套利策略
套利策略会在`_run`函数中自动运行，只需要：
1. 确保已连接交易接口
2. 确保TQSDK行情已订阅ag2604和ag2606
3. 策略会自动监控价差并交易

## 注意事项

### 1. TQSDK认证
如果使用TQSDK的认证功能，需要确保`common/account/tq_account.py`中有`tq_auth`对象。

### 2. 合约数据
`symbol_contract_map`全局变量需要在行情模块连接之前被填充，通常在合约查询回调中完成。

### 3. 线程安全
TQSDK行情处理在独立线程中运行，需要注意线程安全问题。代码中已经使用了适当的同步机制。

### 4. 日志输出
所有日志都通过`gateway.write_log()`输出，确保日志统一管理。

## 后续优化

### 1. 参数配置化
可以将策略参数（如价差阈值、止损点数等）提取到配置文件中。

### 2. 动态合约
目前策略硬编码了ag2604和ag2606，可以改为可配置的合约对。

### 3. 多策略支持
可以在同一个TqSdkMdApi实例中运行多个不同的套利策略。

### 4. 性能统计
添加策略绩效统计功能，记录交易次数、胜率、盈亏等。

## 文件清单

### 新增文件
- `common/tqsdk_gateway.py`：TQSDK行情API通用模块

### 修改文件
- `vnpy_ttstq/vnpy_ttstq/gateway/ttstq_gateway.py`：
  - 添加导入
  - 删除TqSdkMdApi类定义

### 相关文档
- `docs/ag2604_ag2606_spread_trading_strategy.md`：跨期套利策略详细文档

## 版本历史

- **v1.0** (2025-01-27)：初始版本，从vnpy_ttstq中提取TqSdkMdApi到common目录
