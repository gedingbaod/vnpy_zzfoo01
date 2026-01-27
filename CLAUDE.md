# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a customized VeighNa (vnpy) quantitative trading framework installation. VeighNa is a Python-based open-source quantitative trading system framework for professional trading applications. This repository includes the core vnpy framework plus several custom extensions and gateway modules.

**Core Version**: vnpy 4.3.0
**Python Requirements**: Python 3.10+ (3.13 recommended)
**Supported Platforms**: Windows 11+, Windows Server 2022+, Ubuntu 22.04 LTS+, macOS

## Installation and Setup

### Initial Installation

Use the provided installation scripts:

```bash
# Windows
install.bat

# Linux/macOS
bash install.sh
```

These scripts:
1. Upgrade pip and wheel
2. Install ta-lib (technical analysis library)
3. Install the vnpy package in editable mode

### Development Installation

For development mode installation:
```bash
pip install -e .
```

For modules requiring C++ compilation (e.g., TTS gateway):
```bash
pip install -e . --no-build-isolation --config-settings=build-dir=.\vnpy_tts\api
```

## Code Quality Checks

Before committing code, run the following quality checks:

```bash
# Ruff linting (check code style)
ruff check .

# MyPy type checking (check type annotations)
mypy vnpy
```

## Core Architecture

### Event-Driven Framework

VeighNa uses an event-driven architecture centered on:

- **EventEngine** (`vnpy/event/engine.py`): Core event bus that distributes events to registered handlers
- **MainEngine** (`vnpy/trader/engine.py`): Central hub managing gateways, apps, and function engines
- **Event Types** (`vnpy/trader/event.py`): Standard events like EVENT_TICK, EVENT_ORDER, EVENT_TRADE, etc.

### Component Types

1. **Gateway** (`vnpy/trader/gateway.py`): Abstract base class for connecting to different trading systems
   - Examples: CtpGateway, TtsGateway, XtpGateway
   - Methods: `connect()`, `subscribe()`, `send_order()`, `cancel_order()`
   - Callbacks: `on_tick()`, `on_trade()`, `on_order()`, `on_position()`, etc.

2. **App** (`vnpy/trader/app.py`): Application modules that extend functionality
   - Examples: CtaStrategyApp, SpreadTradingApp, DataManagerApp
   - Contains both engine_class (backend logic) and widget_name (UI component)

3. **BaseEngine**: Function engines extending MainEngine capabilities
   - Managed by MainEngine.engines dictionary
   - Accessible via `main_engine.get_engine(engine_name)`

4. **Data Objects** (`vnpy/trader/object.py`): Dataclasses for trading data
   - TickData, BarData, OrderData, TradeData, PositionData, AccountData, ContractData
   - All inherit from BaseData with gateway_name source
   - Use `vt_symbol` format: "SYMBOL.EXCHANGE" (e.g., "IF2501.CFFEX")

5. **Constants** (`vnpy/trader/constant.py`): Trading enums
   - Direction: LONG, SHORT, NET
   - Offset: OPEN, CLOSE, CLOSETODAY, CLOSEYESTERDAY
   - Status: SUBMITTING, NOTTRADED, PARTTRADED, ALLTRADED, CANCELLED, REJECTED
   - Exchange: CFFEX, SHFE, DCE, CZCE, INE, GFEX, SSE, SZSE, etc.

### Project Structure

```
vnpy/                    # Core framework
├── alpha/               # AI/ML strategy module (dataset, model, strategy, lab)
├── chart/               # K-line charting
├── event/               # Event engine
├── rpc/                 # RPC client/server for distributed systems
├── trader/              # Core trading engine
│   ├── engine.py        # MainEngine, BaseEngine
│   ├── gateway.py       # BaseGateway abstract class
│   ├── app.py           # BaseApp abstract class
│   ├── object.py        # Data objects (TickData, OrderData, etc.)
│   ├── constant.py      # Enums and constants
│   └── ui/              # Qt-based GUI
└── [other modules]

vnpy_ctastrategy/        # CTA strategy engine
vnpy_ctabacktester/      # CTA strategy backtesting
vnpy_spreadtrading/      # Spread trading module
vnpy_rqdata/             # RQData datafeed integration
vnpy_dolphindb/          # DolphinDB database adapter
vnpy_datamanager/        # Data management UI
vnpy_sqlite/             # SQLite database adapter (default)
vnpy_ctp/                # CTP gateway (futures)
vnpy_ctptest/            # CTP test/simulation gateway
vnpy_tts/                # TTS simulation gateway
vnpy_ttstq/              # TTS gateway with TQSDK integration
vnpy_tqsdk/              # TQSDK datafeed
vnpy_gm/                 # GM (掘金) datafeed
vnpy_riskmanager/        # Risk management module

common/                  # Project-specific utilities
├── main_contracts.py    # Chinese futures main contracts dictionary
├── vnpy_str.py          # String utilities for symbol/exchange parsing
└── account/             # Account-related utilities

mycode/                  # User code and scripts
testcode/                # Test scripts
examples/                # Example scripts and notebooks
```

## Running the Application

### With GUI (VeighNa Trader)

```bash
python examples/veighna_trader/run.py
```

### Without GUI (Headless)

For production/automated trading:
```bash
python examples/no_ui/run.py
```

This script includes:
- Trading period detection (day/night sessions for Chinese futures)
- Parent/child process architecture for reliability
- Automatic strategy initialization and startup

### Custom Scripts

The project includes custom startup scripts in `mycode/`:
- `run_ui.py`: GUI version
- `run_no_ui.py`: Headless version

## Data Management

### Database Configuration

Default database is SQLite (`vnpy_sqlite`). To use other databases:

- **DolphinDB**: Install `vnpy_dolphindb`, configure in global settings
- **MySQL**: Install `vnpy_mysql` (separate package)
- **PostgreSQL**: Install `vnpy_postgresql` (separate package)

### Data Import

Use DataManagerApp or scripts like `mycode/download_bars_from_rqdata.py` to download market data.

### RQData Integration

Configure RQData in global settings:
```python
{
    "datafeed.name": "rqdata",
    "datafeed.username": "your_username",
    "datafeed.password": "your_token"
}
```

## Gateway Configuration

### CTP (China Futures)

Required settings:
```python
{
    "用户名": "username",
    "密码": "password",
    "经纪商代码": "broker_id",
    "交易服务器": "td_server",
    "行情服务器": "md_server",
    "产品名称": "product_name",
    "授权编码": "auth_code"
}
```

### TTS (Simulation)

For testing, use TTS simulation environment from https://github.com/krenx1983/openctp:
```python
{
    "用户名": "username",
    "密码": "password",
    "经纪商代码": "",
    "交易服务器": "121.36.146.182:20002",
    "行情服务器": "121.36.146.182:20004",
    "产品名称": "",
    "授权编码": ""
}
```

## Strategy Development

### CTA Strategy Template

Located in `vnpy_ctastrategy/base.py`:

```python
from vnpy_ctastrategy import CtaTemplate

class MyStrategy(CtaTemplate):
    author: str = "Your Name"

    # Strategy parameters
    fast_window: int = 10
    slow_window: int = 20

    # Strategy variables
    fast_value: float = 0.0
    slow_value: float = 0.0

    def on_init(self) -> None:
        """Strategy initialization"""
        self.write_log("策略初始化")

    def on_start(self) -> None:
        """Strategy startup"""
        self.write_log("策略启动")

    def on_stop(self) -> None:
        """Strategy stop"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData) -> None:
        """Tick data callback"""

    def on_bar(self, bar: BarData) -> None:
        """Bar data callback - main logic here"""
        # Your strategy logic
        pass
```

### AI/ML Strategies (vnpy.alpha)

The vnpy.alpha module provides end-to-end ML strategy development:

1. **Dataset** (`vnpy/alpha/dataset/`): Feature engineering
   - Alpha101, Alpha158 factor libraries
   - Time-series, cross-section, math, and TA functions

2. **Model** (`vnpy/alpha/model/`): Model training
   - Lasso, LightGBM, MLP implementations
   - Standardized template for custom models

3. **Strategy** (`vnpy/alpha/strategy/`): ML-based strategies
   - Equity cross-sectional strategies
   - Single-instrument time-series strategies

4. **Lab** (`vnpy/alpha/lab.py`): Workflow management
   - Integrates data, model, and backtesting
   - Jupyter notebook examples in `examples/alpha_research/`

## Key Concepts

### Symbol Convention

Always use `vt_symbol` format: `"SYMBOL.EXCHANGE"`
- Correct: `"IF2501.CFFEX"`, `"rb2505.SHFE"`
- Incorrect: `"IF2501"`, `"CFFEX.IF2501"`

### Working Directory

The framework automatically changes working directory to `TRADER_DIR` (usually `~/.vnpy/`) on startup.

### Event Registration

To listen to specific events:
```python
from vnpy.trader.event import EVENT_TICK

def on_tick(event: Event):
    tick: TickData = event.data
    # Process tick

event_engine.register(EVENT_TICK, on_tick)
```

### Gateway-Specific Events

For events on a specific contract:
```python
# Listen to all ticks
event_engine.register(EVENT_TICK, handler)

# Listen to ticks for a specific symbol
event_engine.register(EVENT_TICK + "IF2501.CFFEX", handler)
```

## Chinese Futures Market

### Trading Sessions

- **Day Session**: 8:45 - 15:00
- **Night Session**: 20:45 - 02:45 (next day)

### Main Contracts

Reference `common/main_contracts.py` for a complete list of Chinese futures main contracts by exchange and product symbol.

### Exchanges

- **CFFEX**: China Financial Futures Exchange (stock index futures, bond futures)
- **SHFE**: Shanghai Futures Exchange (metals, rubber, etc.)
- **DCE**: Dalian Commodity Exchange (commodities)
- **CZCE**: Zhengzhou Commodity Exchange (agricultural products, etc.)
- **INE**: Shanghai International Energy Exchange
- **GFEX**: Guangzhou Futures Exchange

## Custom Extensions in This Repository

1. **vnpy_ttstq**: TTS gateway with TQSDK integration
2. **vnpy_ctptest**: CTP test environment gateway
3. **vnpy_ctptq**: CTP gateway with TQSDK integration
4. **common/**: Utility functions for Chinese futures trading
5. **mycode/**: User-developed scripts and strategies

## Testing

```bash
# Run tests (if available)
pytest tests/
```

## Documentation

- Official Documentation: https://www.vnpy.com/docs
- Community Forum: https://www.vnpy.com/forum
- Examples: `examples/` directory
- Notebooks: `examples/alpha_research/` for ML workflows

## Version Information

Check versions in respective module `__init__.py` or `pyproject.toml` files.
