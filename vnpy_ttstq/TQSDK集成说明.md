# vnpy_ttstq TQSDK行情集成说明

## 功能概述

已成功为 `vnpy_ttstq` 模块添加了 TQSDK 作为可选行情源的功能。现在您可以选择使用 TTS 原生行情接口或 TQSDK 来订阅期货合约行情。

## 主要特性

1. **保留原有TTS行情接口**：完全向后兼容，默认使用TTS行情
2. **可选TQSDK行情源**：可通过配置切换到TQSDK行情
3. **环境变量认证**：TQSDK认证信息从环境变量读取，安全便捷
4. **自动容错处理**：如果未安装tqsdk或认证失败，自动回退到TTS行情

## 配置方法

### 1. 安装依赖

首先确保安装了 tqsdk 库：

```bash
pip install tqsdk
```

### 2. 设置环境变量

设置 TQSDK 的认证信息为环境变量：

**Windows (CMD):**
```cmd
set TQSDK_USERNAME=your_username
set TQSDK_PASSWORD=your_password
```

**Windows (PowerShell):**
```powershell
$env:TQSDK_USERNAME="your_username"
$env:TQSDK_PASSWORD="your_password"
```

**Linux/Mac:**
```bash
export TQSDK_USERNAME=your_username
export TQSDK_PASSWORD=your_password
```

或者将环境变量设置到系统环境变量中，这样每次启动都会自动加载。

### 3. 修改网关配置

在使用 `TtstqGateway` 时，在连接配置中添加 `"行情源": "TQSDK"` 字段：

```python
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy_ttstq import TtstqGateway

# 创建引擎
event_engine = EventEngine()
main_engine = MainEngine(event_engine)

# 添加网关
main_engine.add_gateway(TtstqGateway)

# 配置连接信息
tts_setting = {
    "用户名": "your_tts_username",
    "密码": "your_tts_password",
    "经纪商代码": "",
    "交易服务器": "tcp://121.36.146.182:20002",
    "行情服务器": "tcp://121.36.146.182:20004",
    "产品名称": "",
    "授权编码": "",
    "行情源": "TQSDK"  # 使用TQSDK行情源
}

# 连接
main_engine.connect(tts_setting, "TTS")
```

**注意事项：**
- 如果不设置 `"行情源"` 字段或设置为 `"TTS"`，则使用默认的TTS行情接口
- 设置为 `"TQSDK"` 时，需要先设置环境变量，否则会报错

## 代码修改说明

### 1. 新增导入
- 添加了 `tqsdk` 相关导入（带异常处理，如果未安装tqsdk也不会影响原有功能）
- 添加了 `os` 和 `Thread` 导入

### 2. TtstqGateway 类修改
- 在 `default_setting` 中添加了 `"行情源"` 配置项
- 在 `__init__` 中添加了 `tq_md_api` 和 `market_source` 属性
- 修改了 `connect` 方法，根据配置选择行情源
- 修改了 `subscribe` 方法，根据行情源调用相应的订阅API
- 修改了 `close` 方法，正确关闭相应的行情API
- 修改了 `process_timer_event` 方法，仅对TTS行情更新日期

### 3. 新增 TqSdkMdApi 类
- `connect()`: 从环境变量读取认证信息并连接TQSDK
- `_run()`: 独立线程接收行情推送
- `_process_tick()`: 将TQSDK行情数据转换为vnpy的TickData格式
- `subscribe()`: 订阅合约行情
- `close()`: 关闭TQSDK连接

## 使用示例

### 示例1：使用TQSDK行情

```python
import os
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy_ttstq import TtstqGateway
from vnpy_ctastrategy import CtaStrategyApp

# 设置环境变量
os.environ["TQSDK_USERNAME"] = "your_tqsdk_username"
os.environ["TQSDK_PASSWORD"] = "your_tqsdk_password"

# 初始化
event_engine = EventEngine()
main_engine = MainEngine(event_engine)
main_engine.add_gateway(TtstqGateway)
main_engine.add_app(CtaStrategyApp)

# 连接配置（使用TQSDK行情）
tts_setting = {
    "用户名": "tts_user",
    "密码": "tts_pass",
    "经纪商代码": "",
    "交易服务器": "tcp://121.36.146.182:20002",
    "行情服务器": "tcp://121.36.146.182:20004",
    "产品名称": "",
    "授权编码": "",
    "行情源": "TQSDK"  # 关键配置
}

main_engine.connect(tts_setting, "TTS")
```

### 示例2：使用默认TTS行情

```python
# 使用默认TTS行情（不设置行情源或设置为TTS）
tts_setting = {
    "用户名": "tts_user",
    "密码": "tts_pass",
    "经纪商代码": "",
    "交易服务器": "tcp://121.36.146.182:20002",
    "行情服务器": "tcp://121.36.146.182:20004",
    "产品名称": "",
    "授权编码": "",
    # 不设置"行情源"或设置为"TTS"
}
```

## 技术细节

### TQSDK合约代码格式
TQSDK使用 `"交易所.合约代码"` 格式，例如：
- `"CFFEX.IF2501"` - 中金所IF2501合约
- `"SHFE.rb2505"` - 上期所rb2505合约

### 行情数据转换
`TqSdkMdApi` 会自动将TQSDK的行情数据转换为vnpy的 `TickData` 格式，包括：
- 基础行情：最新价、成交量、持仓量等
- 盘口行情：买卖五档价格和数量
- 当日统计：开盘价、最高价、最低价等

### 线程安全
- TQSDK行情接收在独立线程中运行
- 使用 `symbol_contract_map` 全局字典获取合约信息
- 订阅状态通过 `subscribed` 集合管理

## 故障排查

### 问题1：提示"未安装tqsdk库"
**解决方法**：安装tqsdk库
```bash
pip install tqsdk
```

### 问题2：提示"未设置TQSDK认证信息环境变量"
**解决方法**：设置环境变量
```python
import os
os.environ["TQSDK_USERNAME"] = "your_username"
os.environ["TQSDK_PASSWORD"] = "your_password"
```

### 问题3：TQSDK连接失败
**解决方法**：
1. 检查用户名密码是否正确
2. 检查网络连接
3. 查看日志中的具体错误信息
4. 系统会自动回退到TTS行情

### 问题4：没有收到行情数据
**解决方法**：
1. 检查合约代码是否正确
2. 检查合约是否在交易时间
3. 查看日志中的订阅信息
4. 确认 `symbol_contract_map` 中有该合约信息

## 文件修改清单

修改的文件：
- `vnpy_ttstq/vnpy_ttstq/gateway/ttstq_gateway.py`

主要修改内容：
1. 导入部分添加 tqsdk、os、Thread
2. TtstqGateway 类添加行情源选择逻辑
3. 新增 TqSdkMdApi 类（约170行代码）

## 兼容性说明

- **Python版本**：需要 Python 3.10+
- **vnpy版本**：兼容 vnpy 4.0+
- **操作系统**：Windows、Linux、macOS
- **向后兼容**：完全兼容原有TTS行情接口

## 后续优化建议

1. **配置持久化**：可以将行情源配置保存到数据库或配置文件
2. **动态切换**：添加运行时动态切换行情源的功能
3. **行情质量监控**：添加两个行情源的延迟和质量对比
4. **备用行情源**：实现主备行情源自动切换机制

## 联系支持

如有问题或建议，请联系开发团队或提交Issue。
