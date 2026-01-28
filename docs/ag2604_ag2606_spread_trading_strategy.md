# 白银跨期套利策略文档

## 一、策略概述

**交易品种**：ag2604（近月）vs ag2606（远月）跨期套利
**交易方向**：价差回归策略
**交易时间**：仅限当日，收盘前5分钟强制平仓并停止交易
**实现文件**：`vnpy_ttstq/vnpy_ttstq/gateway/ttstq_gateway.py`

## 二、策略参数

```python
# 价差阈值
ag_upper_band = 100           # 上轨：做空差价开仓阈值
ag_middle_band = 35           # 中轨：平仓阈值
ag_lower_band = -30           # 下轨：做多差价开仓阈值

# 交易参数
transaction_volume = 1        # 交易手数（两边相等）
order_timeout = 0.8           # 订单超时时间（秒）
data_sync_tolerance = 0.5     # 数据同步容忍度（秒）

# 风控参数
max_price_limit_ratio = 0.8   # 涨跌停限制比例（80%）
max_spread_cost = 25          # 最大买卖价差成本
stop_loss_points = 50         # 止损点数（固定50点）

# 成本参数
commission_per_side = 120     # 单边手续费（元）
total_commission = 240        # 总手续费（元）
```

## 三、价差定义

```python
# 做空差价（可卖出价差）= near合约买价 - far合约卖价
real_short_spread = near_quote.bid_price1 - far_quote.ask_price1

# 做多差价（可买入价差）= near合约卖价 - far合约买价
real_long_spread = near_quote.ask_price1 - far_quote.bid_price1

# 买卖价差成本（滑点成本）
spread_cost = (near_quote.ask_price1 - near_quote.bid_price1) + \
              (far_quote.ask_price1 - far_quote.bid_price1)
```

## 四、交易逻辑

### 4.1 开仓条件

#### 做空价差（卖近买远）
- **触发条件**：`real_short_spread > ag_upper_band`（价差 > 100）
- **操作**：卖出ag2604（开仓）+ 买入ag2606（开仓）
- **盈利逻辑**：当价差过高时，预期价差会回归到中轨（35）

#### 做多价差（买近卖远）
- **触发条件**：`real_long_spread < ag_lower_band`（价差 < -30）
- **操作**：买入ag2604（开仓）+ 卖出ag2606（开仓）
- **盈利逻辑**：当价差过低（为负）时，预期价差会回归到中轨（35）

### 4.2 平仓条件

#### 持有做空价差持仓
- **触发条件**：`real_long_spread <= ag_middle_band`（价差回归到 <= 35）
- **操作**：买入ag2604（平今）+ 卖出ag2606（平今）

#### 持有多头价差持仓
- **触发条件**：`real_short_spread >= ag_middle_band`（价差回归到 >= 35）
- **操作**：卖出ag2604（平今）+ 买入ag2606（平今）

### 4.3 止损逻辑

#### 做空价差止损
- **触发条件**：当前价差 > 开仓价差 + 50点
- **操作**：立即平仓

#### 做多价差止损
- **触发条件**：当前价差 < 开仓价差 - 50点
- **操作**：立即平仓

### 4.4 强制平仓

#### 时间段
- **日盘上午**：11:25-11:30
- **日盘下午**：14:55-15:00
- **夜盘**：02:25-02:30

#### 操作
- 无论盈亏，强制平仓所有持仓
- 之后不再开新仓

## 五、风控机制

### 5.1 数据同步检查
- **要求**：两个合约的行情时间戳差值在0.5秒以内
- **原因**：数据不同步会导致价差计算不准确

### 5.2 买卖价差成本检查
- **要求**：`spread_cost <= 25`
- **说明**：如果买卖价差过大，说明市场流动性不足或波动剧烈，不宜交易

### 5.3 涨跌停限制
```python
# 计算涨跌停幅度
near_upper_move = (near_quote.upper_limit - near_quote.pre_settlement) * 0.8
near_lower_move = (near_quote.pre_settlement - near_quote.lower_limit) * 0.8

# 如果价格变动超过涨跌停的80%，停止交易
if near_change >= near_upper_move or near_change <= -near_lower_move:
    return  # 停止交易
```

### 5.4 订单超时处理
- **超时时间**：0.8秒
- **处理方式**：
  - 超时未成交的订单自动撤销
  - 检查是否有单腿成交
  - 如果单腿成交，立即平仓已成交的那条腿

### 5.5 单腿成交处理
- **场景**：两条腿中只有一条成交，另一条未成交或被撤销
- **处理**：
  - 立即平仓已成交的那条腿
  - 清空持仓状态
  - 记录日志

### 5.6 收盘前强制平仓
- **判断方式**：通过`Quote.trading_time`获取交易时段
- **操作**：进入收盘前5分钟后，强制平仓所有持仓，不再开新仓

## 六、订单类型

- **开仓/平仓**：`OrderType.MARKET`（市价单）
- **平仓offset**：`Offset.CLOSETODAY`（平今仓）
- **原因**：上期所需要区分今仓昨仓，本策略只做当日交易

## 七、持仓状态管理

### 7.1 数据结构
```python
self.spread_position = {
    "short_spread": {  # 做空价差持仓
        "open_spread": 100.5,          # 开仓时的价差
        "open_time": datetime,          # 开仓时间
        "near_order_id": "order_id",   # ag2604订单ID
        "far_order_id": "order_id",    # ag2606订单ID
        "near_filled": False,          # ag2604是否成交
        "far_filled": False            # ag2606是否成交
    },
    "long_spread": {   # 做多价差持仓
        # 结构同上
    }
}
```

### 7.2 待成交订单跟踪
```python
self.pending_orders = {
    "vt_order_id": {
        "symbol": "ag2604",
        "direction": "SHORT",
        "offset": "OPEN",
        "create_time": timestamp,
        "pair_order_id": "pair_vt_order_id"  # 配对订单ID
    }
}
```

### 7.3 订单状态映射
```python
self.order_status_map = {
    "vt_order_id": Status.ALLTRADED
}
```

## 八、核心函数说明

### 8.1 策略主函数
```python
def _spread_ag2604_ag2606(self, near_quote: Quote, far_quote: Quote) -> None:
    """白银跨期套利策略主函数"""
```
**功能**：执行完整的套利策略逻辑
**调用时机**：每次收到行情更新时（在_run函数中）

### 8.2 时间判断
```python
def _is_closing_time(self, quote: Quote) -> bool:
    """判断是否处于收盘前5分钟"""
```
**功能**：根据Quote.trading_time判断是否接近收盘

### 8.3 涨跌停检查
```python
def _check_price_limit(self, near_quote: Quote, far_quote: Quote, ratio: float) -> bool:
    """检查是否接近涨跌停"""
```
**功能**：检查价格是否接近涨跌停的80%

### 8.4 开仓函数
```python
def _open_short_spread(self, near_quote: Quote, far_quote: Quote,
                      spread: float, volume: int) -> None:
    """开做空价差（卖near买far）"""

def _open_long_spread(self, near_quote: Quote, far_quote: Quote,
                     spread: float, volume: int) -> None:
    """开做多价差（买near卖far）"""
```

### 8.5 平仓函数
```python
def _close_spread_position(self, force: bool = False) -> None:
    """平仓价差持仓"""

def _send_close_orders(self, near_symbol: str, near_direction: Direction,
                      far_symbol: str, far_direction: Direction) -> None:
    """发送平仓订单"""
```

### 8.6 订单管理
```python
def _check_pending_orders_timeout(self, timeout: float) -> None:
    """检查待成交订单超时"""

def _cancel_order(self, vt_orderid: str) -> None:
    """撤销订单"""

def _handle_partial_fill(self) -> None:
    """处理单腿成交情况"""

def _emergency_close_position(self, position_type: str, leg: str) -> None:
    """紧急平仓单腿"""
```

### 8.7 订单状态回调
```python
def on_order_status_update(self, vt_orderid: str, status: Status) -> None:
    """订单状态更新回调（从TtsTdApi调用）"""
```
**功能**：接收TtsTdApi的订单状态更新，维护待成交订单和持仓状态

## 九、TdApi集成

### 9.1 onRtnOrder修改
在`TtsTdApi.onRtnOrder`中添加了通知`TqSdkMdApi`的逻辑：

```python
# 通知TqSdkMdApi订单状态更新（用于套利策略）
vt_orderid: str = f"{self.gateway_name}.{orderid}"
if self.gateway.tq_md_api:
    self.gateway.tq_md_api.on_order_status_update(vt_orderid, order.status)
```

### 9.2 数据流
1. TTS交易接口推送订单状态 → `TtsTdApi.onRtnOrder`
2. `TtsTdApi`调用 → `TqSdkMdApi.on_order_status_update`
3. `TqSdkMdApi`更新订单状态映射和持仓状态
4. 策略根据最新状态执行交易逻辑

## 十、日志记录

策略会记录以下关键日志：

### 10.1 开仓相关
- 做空/做多价差开仓触发原因
- 订单发送成功/失败
- 订单成交状态

### 10.2 平仓相关
- 价差回归平仓
- 止损平仓
- 收盘前强制平仓

### 10.3 风控相关
- 买卖价差成本过大
- 接近涨跌停停止交易
- 订单超时撤销
- 单腿成交紧急处理

### 10.4 异常相关
- 所有异常都会被捕获并记录
- 不会导致策略崩溃

## 十一、使用说明

### 11.1 启动策略
1. 确保已连接TTS交易接口
2. 确保TQSDK行情已订阅ag2604和ag2606
3. 策略会自动运行，无需手动干预

### 11.2 监控策略
- 通过日志文件监控策略运行状态
- 关键日志包括：
  - 开仓/平仓操作
  - 订单状态
  - 持仓变化
  - 异常情况

### 11.3 停止策略
- 断开TTS连接即可停止策略
- 策略会在收盘前自动平仓

## 十二、优化建议

### 12.1 参数优化
- 根据历史数据优化价差阈值（ag_upper_band、ag_middle_band、ag_lower_band）
- 根据实际交易情况调整止损点数（stop_loss_points）
- 根据市场波动性调整订单超时时间（order_timeout）

### 12.2 功能扩展
- 添加动态手续费计算
- 添加更精细的滑点成本计算
- 添加最大回撤控制
- 添加资金管理（仓位控制）
- 添加策略绩效统计

### 12.3 性能优化
- 优化数据同步检查逻辑
- 减少不必要的日志输出
- 优化订单状态更新频率

## 十三、风险提示

### 13.1 策略风险
- **价差不回归风险**：价差可能继续扩大或缩小，导致止损
- **单腿成交风险**：虽然有处理机制，但仍可能造成损失
- **流动性风险**：市场流动性不足时可能无法及时成交
- **涨停跌停风险**：合约涨跌停可能导致无法平仓

### 13.2 系统风险
- **网络延迟**：可能导致订单超时或单腿成交
- **行情中断**：TQSDK行情中断会导致策略停止
- **交易中断**：TTS交易中断可能导致无法平仓

### 13.3 操作建议
- 充分测试后再实盘使用
- 始终关注策略运行日志
- 准备应急预案（手动干预）
- 控制仓位规模
- 设置合理的止损

## 十四、参考资料

### 14.1 TQSDK文档
- Quote对象说明：https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.objs.html#tqsdk.objs.Quote
- API文档：https://doc.shinnytech.com/tqsdk/latest/

### 14.2 VeighNa文档
- 官方文档：https://www.vnpy.com/docs
- 社区论坛：https://www.vnpy.com/forum

### 14.3 相关代码文件
- `vnpy_ttstq/vnpy_ttstq/gateway/ttstq_gateway.py`：策略实现
- `vnpy/trader/constant.py`：交易常量定义
- `vnpy/trader/object.py`：数据对象定义

---

**文档版本**：v1.0
**创建日期**：2025-01-27
**最后更新**：2025-01-27
**维护者**：Claude Code
