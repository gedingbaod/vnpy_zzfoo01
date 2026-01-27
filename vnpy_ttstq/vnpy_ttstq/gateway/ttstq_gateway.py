from __future__ import annotations

import asyncio
import sys
import os
from datetime import datetime, timedelta
from time import sleep
from threading import Thread, Condition
from typing import Any, Union

from tqsdk.objs import Quote

from common.account.tq_account import tq_auth
from common.vnpy_time import datetime_format, get_now
from vnpy.event.engine import EventEngine
from pathlib import Path

from vnpy.trader.constant import (
    Direction,
    Offset,
    Exchange,
    OrderType,
    Product,
    Status,
    OptionType
)
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData,
    OrderData,
    TradeData,
    PositionData,
    AccountData,
    ContractData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
)
from vnpy.trader.utility import get_folder_path, ZoneInfo
from vnpy.trader.event import EVENT_TIMER
from vnpy.event import Event

try:
    from tqsdk import TqApi, TqAuth
    TQSDK_AVAILABLE = True
except ImportError:
    TQSDK_AVAILABLE = False

from ..api import (
    MdApi,
    TdApi,
    THOST_FTDC_OAS_Submitted,
    THOST_FTDC_OAS_Accepted,
    THOST_FTDC_OAS_Rejected,
    THOST_FTDC_OST_NoTradeQueueing,
    THOST_FTDC_OST_PartTradedQueueing,
    THOST_FTDC_OST_AllTraded,
    THOST_FTDC_OST_Canceled,
    THOST_FTDC_D_Buy,
    THOST_FTDC_D_Sell,
    THOST_FTDC_PD_Long,
    THOST_FTDC_PD_Short,
    THOST_FTDC_OPT_LimitPrice,
    THOST_FTDC_OPT_AnyPrice,
    THOST_FTDC_OF_Open,
    THOST_FTDC_OFEN_Close,
    THOST_FTDC_OFEN_CloseYesterday,
    THOST_FTDC_OFEN_CloseToday,
    THOST_FTDC_PC_Futures,
    THOST_FTDC_PC_Options,
    THOST_FTDC_PC_SpotOption,
    THOST_FTDC_PC_Combination,
    THOST_FTDC_CP_CallOptions,
    THOST_FTDC_CP_PutOptions,
    THOST_FTDC_HF_Speculation,
    THOST_FTDC_CC_Immediately,
    THOST_FTDC_FCC_NotForceClose,
    THOST_FTDC_TC_GFD,
    THOST_FTDC_VC_AV,
    THOST_FTDC_TC_IOC,
    THOST_FTDC_VC_CV,
    THOST_FTDC_AF_Delete
)


# 委托状态映射
STATUS_TTS2VT: dict[str, Status] = {
    THOST_FTDC_OAS_Submitted: Status.SUBMITTING,
    THOST_FTDC_OAS_Accepted: Status.SUBMITTING,
    THOST_FTDC_OAS_Rejected: Status.REJECTED,
    THOST_FTDC_OST_NoTradeQueueing: Status.NOTTRADED,
    THOST_FTDC_OST_PartTradedQueueing: Status.PARTTRADED,
    THOST_FTDC_OST_AllTraded: Status.ALLTRADED,
    THOST_FTDC_OST_Canceled: Status.CANCELLED
}

# 多空方向映射
DIRECTION_VT2TTS: dict[Direction, str] = {
    Direction.LONG: THOST_FTDC_D_Buy,
    Direction.SHORT: THOST_FTDC_D_Sell
}
DIRECTION_TTS2VT: dict[str, Direction] = {v: k for k, v in DIRECTION_VT2TTS.items()}
DIRECTION_TTS2VT[THOST_FTDC_PD_Long] = Direction.LONG
DIRECTION_TTS2VT[THOST_FTDC_PD_Short] = Direction.SHORT

# 委托类型映射
ORDERTYPE_VT2TTS: dict[OrderType, str] = {
    OrderType.LIMIT: THOST_FTDC_OPT_LimitPrice,
    OrderType.MARKET: THOST_FTDC_OPT_AnyPrice
}
ORDERTYPE_TTS2VT: dict[str, OrderType] = {v: k for k, v in ORDERTYPE_VT2TTS.items()}

# 开平方向映射
OFFSET_VT2TTS: dict[Offset, str] = {
    Offset.OPEN: THOST_FTDC_OF_Open,
    Offset.CLOSE: THOST_FTDC_OFEN_Close,
    Offset.CLOSETODAY: THOST_FTDC_OFEN_CloseToday,
    Offset.CLOSEYESTERDAY: THOST_FTDC_OFEN_CloseYesterday,
}
OFFSET_TTS2VT: dict[str, Offset] = {v: k for k, v in OFFSET_VT2TTS.items()}

# 交易所映射
EXCHANGE_TTS2VT: dict[str, Exchange] = {
    "CFFEX": Exchange.CFFEX,
    "SHFE": Exchange.SHFE,
    "CZCE": Exchange.CZCE,
    "DCE": Exchange.DCE,
    "GFEX": Exchange.GFEX,
    "INE": Exchange.INE,
    "SSE": Exchange.SSE,
    "SZSE": Exchange.SZSE,
    "NASD": Exchange.NASDAQ,
    "NYSE": Exchange.NYSE,
    "HKEX": Exchange.SEHK,
}
EXCHANGE_VT2TTS: dict[Exchange, str] = {v: k for k, v in EXCHANGE_TTS2VT.items()}

# 产品类型映射
PRODUCT_TTS2VT: dict[str, Product] = {
    THOST_FTDC_PC_Futures: Product.FUTURES,
    THOST_FTDC_PC_Options: Product.OPTION,
    THOST_FTDC_PC_SpotOption: Product.OPTION,
    THOST_FTDC_PC_Combination: Product.SPREAD,
    'E': Product.EQUITY,
    'B': Product.BOND,
    'D': Product.FUND
}

# 期权类型映射
OPTIONTYPE_TTS2VT: dict[str, OptionType] = {
    THOST_FTDC_CP_CallOptions: OptionType.CALL,
    THOST_FTDC_CP_PutOptions: OptionType.PUT
}

# 其他常量
MAX_FLOAT = sys.float_info.max                  # 浮点数极限值
CHINA_TZ = ZoneInfo("Asia/Shanghai")       # 中国时区

# 合约数据全局缓存字典
symbol_contract_map: dict[str, ContractData] = {}

# 差价交易
ag_upper_band = 100
ag_middle_band = 35
ag_lower_band = -30


class TtstqGateway(BaseGateway):
    """
    VeighNa用于对接期货TTS柜台的交易接口。
    """

    default_name: str = "TTS"

    default_setting: dict[str, str] = {
        "用户名": "",
        "密码": "",
        "经纪商代码": "",
        "交易服务器": "",
        "行情服务器": "",
        "产品名称": "",
        "授权编码": "",
        "行情源": "TTS",  # TTS 或 TQSDK
    }

    exchanges: list[str] = list(EXCHANGE_TTS2VT.values())

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.td_api: TtsTdApi = TtsTdApi(self)
        self.md_api: TtsMdApi = TtsMdApi(self)
        self.tq_md_api: Union[TqSdkMdApi, None] = None  # TQSDK行情API

        self.count: int = 0
        self.market_source: str = "TTS"  # 默认使用TTS行情
        self.update_pos_condition: Condition = Condition()

    def connect(self, setting: dict) -> None:
        """连接交易接口"""
        userid: str = setting["用户名"]
        password: str = setting["密码"]
        brokerid: str = setting["经纪商代码"]
        td_address: str = setting["交易服务器"]
        md_address: str = setting["行情服务器"]
        appid: str = setting["产品名称"]
        auth_code: str = setting["授权编码"]
        market_source: str = setting.get("行情源", "TTS")  # 获取行情源配置
        # market_source = check_tqsdk(market_source)

        self.market_source = market_source.upper()  # 保存行情源配置

        if (
            (not td_address.startswith("tcp://"))
            and (not td_address.startswith("ssl://"))
        ):
            td_address = "tcp://" + td_address

        if (
            (not md_address.startswith("tcp://"))
            and (not md_address.startswith("ssl://"))
        ):
            md_address = "tcp://" + md_address

        # 连接交易接口
        self.td_api.connect(td_address, userid, password, brokerid, auth_code, appid)

        # 根据配置选择行情源
        if self.market_source == "TQSDK":
            if not TQSDK_AVAILABLE:
                self.write_log("警告：未安装tqsdk库，无法使用TQSDK行情源，将使用TTS行情源")
                self.market_source = "TTS"
                self.md_api.connect(md_address, userid, password, brokerid)
            else:
                self.tq_md_api = TqSdkMdApi(self)
                # 连接之前，默认是TTS，tq_md_api.subscribed为空，但是会有订阅输入
                # 所以在TQSDK连接时，把这部分订阅，加入到TQSDK的订阅里
                # TQSDK会在连接后，把这部分订阅执行
                self.tq_md_api.subscribed.update(self.md_api.subscribed)
                self.tq_md_api.connect()

        else:  # 默认使用TTS行情
            self.md_api.connect(md_address, userid, password, brokerid)

        self.init_query()

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        # if self.market_source == "TQSDK" and self.tq_md_api:
        if self.market_source == "TQSDK":
            self.tq_md_api.subscribe(req)
        else:  # 默认使用TTS行情
            self.md_api.subscribe(req)

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        # 期权询价
        if req.type == OrderType.RFQ:
            vt_orderid: str = self.td_api.send_rfq(req)
        # 其他委托
        else:
            vt_orderid = self.td_api.send_order(req)
        return vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        self.td_api.cancel_order(req)

    def query_account(self) -> None:
        """查询资金"""
        self.td_api.query_account()

    def query_position(self) -> None:
        """查询持仓"""
        self.td_api.query_position()

    def close(self) -> None:
        """关闭接口"""
        self.td_api.close()

        # 根据行情源关闭相应的行情API
        if self.market_source == "TQSDK" and self.tq_md_api:
            self.tq_md_api.close()
        else:
            self.md_api.close()

    def write_error(self, msg: str, error: dict) -> None:
        """输出错误信息日志"""
        error_id: int = error["ErrorID"]
        error_msg: str = error["ErrorMsg"]
        msg = f"{msg}，代码：{error_id}，信息：{error_msg}"
        self.write_log(msg)

    def process_timer_event(self, event: Event) -> None:
        """定时事件处理"""
        # 调用两次，实际执行一次
        self.count += 1
        if self.count < 2:
            return
        self.count = 0

        # 0是query_account, 1是query_position
        func = self.query_functions.pop(0)
        func()
        self.query_functions.append(func)

        # 根据行情源更新日期
        if self.market_source == "TQSDK" and self.tq_md_api:
            pass  # TQSDK不需要更新日期
        else:
            self.md_api.update_date()

    def init_query(self) -> None:
        """初始化查询任务"""
        self.count = 0
        self.query_functions: list = [self.query_account, self.query_position]
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)


class TtsMdApi(MdApi):
    """"""

    def __init__(self, gateway: TtstqGateway) -> None:
        """构造函数"""
        super().__init__()

        self.gateway: TtstqGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.reqid: int = 0

        self.connect_status: bool = False
        self.login_status: bool = False
        self.subscribed: set = set()

        self.userid: str = ""
        self.password: str = ""
        self.brokerid: str = ""

        self.current_date: str = datetime.now().strftime("%Y%m%d")

    def onFrontConnected(self) -> None:
        """服务器连接成功回报"""
        self.gateway.write_log("行情服务器连接成功")
        self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """服务器连接断开回报"""
        self.login_status = False
        self.gateway.write_log(f"行情服务器连接断开，原因{reason}")

    def onRspUserLogin(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """用户登录请求回报"""
        if not error["ErrorID"]:
            self.login_status = True
            self.gateway.write_log("行情服务器登录成功")

            for symbol in self.subscribed:
                self.subscribeMarketData(symbol)
        else:
            self.gateway.write_error("行情服务器登录失败", error)

    def onRspError(self, error: dict, reqid: int, last: bool) -> None:
        """请求报错回报"""
        self.gateway.write_error("行情接口报错", error)

    def onRspSubMarketData(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """订阅行情回报"""
        if not error or not error["ErrorID"]:
            return

        self.gateway.write_error("行情订阅失败", error)

    def onRtnDepthMarketData(self, data: dict) -> None:
        """行情数据推送"""
        # 过滤没有时间戳的异常行情数据
        if not data["UpdateTime"]:
            return

        symbol: str = data["InstrumentID"]
        contract: ContractData = symbol_contract_map.get(symbol, None)
        if not contract:
            return

        timestamp: str = f"{self.current_date} {data['UpdateTime']}.{int(data['UpdateMillisec']/100)}"
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d %H:%M:%S.%f")
        dt = dt.replace(tzinfo=CHINA_TZ)

        tick: TickData = TickData(
            symbol=symbol,
            exchange=contract.exchange,
            datetime=dt,
            name=contract.name,
            volume=data["Volume"],
            turnover=data["Turnover"],
            open_interest=data["OpenInterest"],
            last_price=adjust_price(data["LastPrice"]),
            limit_up=data["UpperLimitPrice"],
            limit_down=data["LowerLimitPrice"],
            open_price=adjust_price(data["OpenPrice"]),
            high_price=adjust_price(data["HighestPrice"]),
            low_price=adjust_price(data["LowestPrice"]),
            pre_close=adjust_price(data["PreClosePrice"]),
            bid_price_1=adjust_price(data["BidPrice1"]),
            ask_price_1=adjust_price(data["AskPrice1"]),
            bid_volume_1=data["BidVolume1"],
            ask_volume_1=data["AskVolume1"],
            gateway_name=self.gateway_name
        )

        if data["BidVolume2"] or data["AskVolume2"]:
            tick.bid_price_2 = adjust_price(data["BidPrice2"])
            tick.bid_price_3 = adjust_price(data["BidPrice3"])
            tick.bid_price_4 = adjust_price(data["BidPrice4"])
            tick.bid_price_5 = adjust_price(data["BidPrice5"])

            tick.ask_price_2 = adjust_price(data["AskPrice2"])
            tick.ask_price_3 = adjust_price(data["AskPrice3"])
            tick.ask_price_4 = adjust_price(data["AskPrice4"])
            tick.ask_price_5 = adjust_price(data["AskPrice5"])

            tick.bid_volume_2 = data["BidVolume2"]
            tick.bid_volume_3 = data["BidVolume3"]
            tick.bid_volume_4 = data["BidVolume4"]
            tick.bid_volume_5 = data["BidVolume5"]

            tick.ask_volume_2 = data["AskVolume2"]
            tick.ask_volume_3 = data["AskVolume3"]
            tick.ask_volume_4 = data["AskVolume4"]
            tick.ask_volume_5 = data["AskVolume5"]

        self.gateway.on_tick(tick)

    def connect(self, address: str, userid: str, password: str, brokerid: str) -> None:
        """连接服务器"""
        self.userid = userid
        self.password = password
        self.brokerid = brokerid

        # 禁止重复发起连接，会导致异常崩溃
        if not self.connect_status:
            path: Path = get_folder_path(self.gateway_name.lower())
            self.createFtdcMdApi((str(path) + "\\Md").encode("GBK"))

            self.registerFront(address)
            self.init()

            self.connect_status = True

    def login(self) -> None:
        """用户登录"""
        tts_req: dict = {
            "UserID": self.userid,
            "Password": self.password,
            "BrokerID": self.brokerid
        }

        self.reqid += 1
        self.reqUserLogin(tts_req, self.reqid)

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        symbol: str = req.symbol

        # 过滤重复的订阅
        if symbol in self.subscribed:
            return
        # 订阅逻辑分已连接和未连接
        # 已连接情况，订阅合约，加入订阅列表
        # 未连接情况，只加入订阅列表，在连接后会再调用一次订阅
        if self.login_status:
            self.subscribeMarketData(req.symbol)
        self.subscribed.add(req.symbol)

    def close(self) -> None:
        """关闭连接"""
        if self.connect_status:
            self.exit()

    def update_date(self) -> None:
        """更新当前日期"""
        self.current_date = datetime.now().strftime("%Y%m%d")


class TtsTdApi(TdApi):
    """"""

    def __init__(self, gateway: TtstqGateway) -> None:
        """构造函数"""
        super().__init__()

        self.gateway: TtstqGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.reqid: int = 0
        self.order_ref: int = 0

        self.connect_status: bool = False
        self.login_status: bool = False
        self.auth_status: bool = False
        self.login_failed: bool = False
        self.contract_inited: bool = False

        self.userid: str = ""
        self.password: str = ""
        self.brokerid: str = ""
        self.auth_code: str = ""
        self.appid: str = ""

        self.frontid: int = 0
        self.sessionid: int = 0

        self.order_data: list[dict] = []
        self.trade_data: list[dict] = []
        self.positions: dict[str, PositionData] = {}
        self.positions_for_tqsdk: dict[str, PositionData] = {}
        self.sysid_orderid_map: dict[str, str] = {}

    def onFrontConnected(self) -> None:
        """服务器连接成功回报"""
        self.gateway.write_log("交易服务器连接成功")

        if self.auth_code:
            self.authenticate()
        else:
            self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """服务器连接断开回报"""
        self.login_status = False
        self.gateway.write_log(f"交易服务器连接断开，原因{reason}")

    def onRspAuthenticate(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """用户授权验证回报"""
        if not error['ErrorID']:
            self.auth_status = True
            self.gateway.write_log("交易服务器授权验证成功")
            self.login()
        else:
            self.gateway.write_error("交易服务器授权验证失败", error)

    def onRspUserLogin(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """用户登录请求回报"""
        if not error["ErrorID"]:
            self.frontid = data["FrontID"]
            self.sessionid = data["SessionID"]
            self.login_status = True
            self.gateway.write_log("交易服务器登录成功")

            # 自动确认结算单
            tts_req: dict = {
                "BrokerID": self.brokerid,
                "InvestorID": self.userid
            }
            self.reqid += 1
            self.reqSettlementInfoConfirm(tts_req, self.reqid)
        else:
            self.login_failed = True

            self.gateway.write_error("交易服务器登录失败", error)

    def onRspOrderInsert(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """委托下单失败回报"""
        time_get = get_now()
        self.gateway.write_log(f"收到CTP回调时间:{time_get}")

        order_ref: str = data["OrderRef"]
        orderid: str = f"{self.frontid}_{self.sessionid}_{order_ref}"

        symbol: str = data["InstrumentID"]
        contract: ContractData = symbol_contract_map[symbol]

        order: OrderData = OrderData(
            symbol=symbol,
            exchange=contract.exchange,
            orderid=orderid,
            direction=DIRECTION_TTS2VT[data["Direction"]],
            offset=OFFSET_TTS2VT.get(data["CombOffsetFlag"], Offset.NONE),
            price=data["LimitPrice"],
            volume=data["VolumeTotalOriginal"],
            status=Status.REJECTED,
            gateway_name=self.gateway_name
        )
        self.gateway.on_order(order)

        self.gateway.write_error("交易委托失败", error)

    def onRspOrderAction(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """委托撤单失败回报"""
        self.gateway.write_error("交易撤单失败", error)

    def onRspSettlementInfoConfirm(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """确认结算单回报"""
        self.gateway.write_log("结算信息确认成功")

        # 由于流控，单次查询可能失败，通过while循环持续尝试，直到成功发出请求
        while True:
            self.reqid += 1
            n: int = self.reqQryInstrument({}, self.reqid)

            if not n:
                break
            else:
                sleep(1)

    def onRspQryInvestorPosition(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """持仓查询回报"""
        if not data:
            return

        # 必须已经收到了合约信息后才能处理
        symbol: str = data["InstrumentID"]
        contract: ContractData = symbol_contract_map.get(symbol, None)
        # 因为每次收到数据是多条，通过last判断是不是最后一条，所以使用positions缓存所有数据，如果是最后一条再清空缓存
        if contract:
            # 获取之前缓存的持仓数据缓存
            key: str = f"{data['InstrumentID'], data['PosiDirection']}"
            position: PositionData = self.positions.get(key, None)
            if not position:
                position = PositionData(
                    symbol=data["InstrumentID"],
                    exchange=contract.exchange,
                    direction=DIRECTION_TTS2VT[data["PosiDirection"]],
                    gateway_name=self.gateway_name
                )
                self.positions[key] = position

            # 对于上期所昨仓需要特殊处理
            if position.exchange in [Exchange.SHFE, Exchange.INE]:
                if data["YdPosition"] and not data["TodayPosition"]:
                    position.yd_volume = data["Position"]
            # 对于其他交易所昨仓的计算
            else:
                position.yd_volume = data["Position"] - data["TodayPosition"]

            # 获取合约的乘数信息
            size: int = contract.size

            # 计算之前已有仓位的持仓总成本
            cost: float = position.price * position.volume * size

            # 累加更新持仓数量和盈亏
            position.volume += data["Position"]
            position.pnl += data["PositionProfit"]

            # 计算更新后的持仓总成本和均价
            if position.volume and size:
                cost += data["PositionCost"]
                position.price = cost / (position.volume * size)

            # 更新仓位冻结数量
            if position.direction == Direction.LONG:
                position.frozen += data["ShortFrozen"]
            else:
                position.frozen += data["LongFrozen"]

        if last:
            for position in self.positions.values():
                self.gateway.on_position(position)

            # 这里已经清空，之后的处理是拿不到仓位数量的
            # 所以要存入临时变量，供tqsdk调用
            # 因为会有同步问题，所以加锁
            with self.gateway.update_pos_condition:
                self.positions_for_tqsdk.update(self.positions)
                self.positions.clear()
                self.gateway.update_pos_condition.notify()

    def onRspQryTradingAccount(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """资金查询回报"""
        if "AccountID" not in data:
            return

        account: AccountData = AccountData(
            accountid=data["AccountID"],
            balance=data["Balance"],
            frozen=data["FrozenMargin"] + data["FrozenCash"] + data["FrozenCommission"],
            gateway_name=self.gateway_name
        )
        account.available = data["Available"]

        self.gateway.on_account(account)

    def onRspQryInstrument(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """合约查询回报"""
        product: Product = PRODUCT_TTS2VT.get(data["ProductClass"], None)
        exchange: Exchange = EXCHANGE_TTS2VT.get(data["ExchangeID"], None)
        if product and exchange:
            contract: ContractData = ContractData(
                symbol=data["InstrumentID"],
                exchange=exchange,
                name=data["InstrumentName"],
                product=product,
                size=data["VolumeMultiple"],
                pricetick=data["PriceTick"],
                gateway_name=self.gateway_name
            )

            # 期权相关
            if contract.product == Product.OPTION:
                # 移除郑商所期权产品名称带有的C/P后缀
                if contract.exchange == Exchange.CZCE:
                    contract.option_portfolio = data["ProductID"][:-1]
                else:
                    contract.option_portfolio = data["ProductID"]

                contract.option_underlying = data["UnderlyingInstrID"]
                contract.option_type = OPTIONTYPE_TTS2VT.get(data["OptionsType"], None)
                contract.option_strike = data["StrikePrice"]
                contract.option_index = str(data["StrikePrice"])
                contract.option_expiry = datetime.strptime(data["ExpireDate"], "%Y%m%d")

            elif contract.product == Product.EQUITY or contract.product == Product.FUND:
                if exchange in [Exchange.SSE, Exchange.SZSE]:
                    contract.min_volume = 100
            elif contract.product == Product.BOND and exchange in [Exchange.SSE, Exchange.SZSE]:
                contract.min_volume = 10

            self.gateway.on_contract(contract)

            symbol_contract_map[contract.symbol] = contract

        if last:
            self.contract_inited = True
            self.gateway.write_log("合约信息查询成功")

            for data in self.order_data:
                self.onRtnOrder(data)
            self.order_data.clear()

            for data in self.trade_data:
                self.onRtnTrade(data)
            self.trade_data.clear()

    def onRtnOrder(self, data: dict) -> None:
        """委托更新推送"""
        if not self.contract_inited:
            self.order_data.append(data)
            return

        symbol: str = data["InstrumentID"]
        contract: ContractData = symbol_contract_map[symbol]

        frontid: int = data["FrontID"]
        sessionid: int = data["SessionID"]
        order_ref: str = data["OrderRef"]
        orderid: str = f"{frontid}_{sessionid}_{order_ref}"

        time_order = get_now()
        self.gateway.write_log(f'获取到订单的时间：{time_order} 订单ID：{orderid} 订单状态：{STATUS_TTS2VT[data["OrderStatus"]]}')

        timestamp: str = f"{data['InsertDate']} {data['InsertTime']}"
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d %H:%M:%S")
        dt = dt.replace(tzinfo=CHINA_TZ)

        order: OrderData = OrderData(
            symbol=symbol,
            exchange=contract.exchange,
            orderid=orderid,
            type=ORDERTYPE_TTS2VT[data["OrderPriceType"]],
            direction=DIRECTION_TTS2VT[data["Direction"]],
            offset=OFFSET_TTS2VT[data["CombOffsetFlag"]],
            price=data["LimitPrice"],
            volume=data["VolumeTotalOriginal"],
            traded=data["VolumeTraded"],
            status=STATUS_TTS2VT[data["OrderStatus"]],
            datetime=dt,
            gateway_name=self.gateway_name
        )
        time_order = get_now()
        self.gateway.write_log(
            f'整理订单时间：{time_order} 订单信息：{order}')
        self.gateway.on_order(order)

        self.sysid_orderid_map[data["OrderSysID"]] = orderid

    def onRtnTrade(self, data: dict) -> None:
        """成交数据推送"""
        if not self.contract_inited:
            self.trade_data.append(data)
            return

        symbol: str = data["InstrumentID"]
        contract: ContractData = symbol_contract_map[symbol]

        orderid: str = self.sysid_orderid_map[data["OrderSysID"]]

        timestamp: str = f"{data['TradeDate']} {data['TradeTime']}"
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d %H:%M:%S")
        dt = dt.replace(tzinfo=CHINA_TZ)

        trade: TradeData = TradeData(
            symbol=symbol,
            exchange=contract.exchange,
            orderid=orderid,
            tradeid=data["TradeID"],
            direction=DIRECTION_TTS2VT[data["Direction"]],
            offset=OFFSET_TTS2VT[data["OffsetFlag"]],
            price=data["Price"],
            volume=data["Volume"],
            datetime=dt,
            gateway_name=self.gateway_name
        )
        self.gateway.on_trade(trade)

    def onRspForQuoteInsert(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """询价请求回报"""
        if not error["ErrorID"]:
            symbol: str = data["InstrumentID"]
            msg: str = f"{symbol}询价请求发送成功"
            self.gateway.write_log(msg)
        else:
            self.gateway.write_error("询价请求发送失败", error)

    def connect(
        self,
        address: str,
        userid: str,
        password: str,
        brokerid: str,
        auth_code: str,
        appid: str
    ) -> None:
        """连接服务器"""
        self.userid = userid
        self.password = password
        self.brokerid = brokerid
        self.auth_code = auth_code
        self.appid = appid

        if not self.connect_status:
            path: Path = get_folder_path(self.gateway_name.lower())
            self.createFtdcTraderApi((str(path) + "\\Td").encode("GBK"))

            self.subscribePrivateTopic(0)
            self.subscribePublicTopic(0)

            self.registerFront(address)
            self.init()

            self.connect_status = True
        else:
            self.authenticate()

    def authenticate(self) -> None:
        """发起授权验证"""
        tts_req: dict = {
            "UserID": self.userid,
            "BrokerID": self.brokerid,
            "AuthCode": self.auth_code,
            "AppID": self.appid
        }

        self.reqid += 1
        self.reqAuthenticate(tts_req, self.reqid)

    def login(self) -> None:
        """用户登录"""
        if self.login_failed:
            return

        tts_req: dict = {
            "UserID": self.userid,
            "Password": self.password,
            "BrokerID": self.brokerid,
            "AppID": self.appid
        }

        self.reqid += 1
        self.reqUserLogin(tts_req, self.reqid)

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        if req.offset not in OFFSET_VT2TTS:
            self.gateway.write_log("请选择开平方向")
            return ""

        if req.type not in ORDERTYPE_VT2TTS:
            self.gateway.write_log(f"当前接口不支持该类型的委托{req.type.value}")
            return ""

        exchange: Exchange = EXCHANGE_VT2TTS.get(req.exchange, None)
        if not exchange:
            self.gateway.write_log(f"不支持的交易所：{req.exchange}")
            return ""

        self.order_ref += 1

        tts_req: dict = {
            "InstrumentID": req.symbol,
            "ExchangeID": exchange,
            "LimitPrice": req.price,
            "VolumeTotalOriginal": int(req.volume),
            "OrderPriceType": ORDERTYPE_VT2TTS.get(req.type, ""),
            "Direction": DIRECTION_VT2TTS.get(req.direction, ""),
            "CombOffsetFlag": OFFSET_VT2TTS.get(req.offset, ""),
            "OrderRef": str(self.order_ref),
            "InvestorID": self.userid,
            "UserID": self.userid,
            "BrokerID": self.brokerid,
            "CombHedgeFlag": THOST_FTDC_HF_Speculation,
            "ContingentCondition": THOST_FTDC_CC_Immediately,
            "ForceCloseReason": THOST_FTDC_FCC_NotForceClose,
            "IsAutoSuspend": 0,
            "TimeCondition": THOST_FTDC_TC_GFD,
            "VolumeCondition": THOST_FTDC_VC_AV,
            "MinVolume": 1
        }

        if req.type == OrderType.FAK:
            tts_req["OrderPriceType"] = THOST_FTDC_OPT_LimitPrice
            tts_req["TimeCondition"] = THOST_FTDC_TC_IOC
            tts_req["VolumeCondition"] = THOST_FTDC_VC_AV
        elif req.type == OrderType.FOK:
            tts_req["OrderPriceType"] = THOST_FTDC_OPT_LimitPrice
            tts_req["TimeCondition"] = THOST_FTDC_TC_IOC
            tts_req["VolumeCondition"] = THOST_FTDC_VC_CV

        self.reqid += 1
        time_send = get_now()
        self.gateway.write_log(f"CTP接口下单时间:{time_send} ")
        self.reqOrderInsert(tts_req, self.reqid)

        orderid: str = f"{self.frontid}_{self.sessionid}_{self.order_ref}"
        order: OrderData = req.create_order_data(orderid, self.gateway_name)
        self.gateway.on_order(order)

        vt_orderid: str = order.vt_orderid
        return vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        exchange: Exchange = EXCHANGE_VT2TTS.get(req.exchange, None)
        if not exchange:
            self.gateway.write_log(f"不支持的交易所：{req.exchange}")
            return

        frontid, sessionid, order_ref = req.orderid.split("_")

        tts_req: dict = {
            "InstrumentID": req.symbol,
            "ExchangeID": exchange,
            "OrderRef": order_ref,
            "FrontID": int(frontid),
            "SessionID": int(sessionid),
            "ActionFlag": THOST_FTDC_AF_Delete,
            "BrokerID": self.brokerid,
            "InvestorID": self.userid
        }

        self.reqid += 1
        self.reqOrderAction(tts_req, self.reqid)

    def send_rfq(self, req: OrderRequest) -> str:
        """询价请求"""
        exchange: Exchange = EXCHANGE_VT2TTS.get(req.exchange, None)
        if not exchange:
            self.gateway.write_log(f"不支持的交易所：{req.exchange}")
            return ""

        self.order_ref += 1

        tts_req: dict = {
            "InstrumentID": req.symbol,
            "ExchangeID": exchange,
            "ForQuoteRef": str(self.order_ref),
            "BrokerID": self.brokerid,
            "InvestorID": self.userid
        }

        self.reqid += 1
        self.reqForQuoteInsert(tts_req, self.reqid)

        orderid: str = f"{self.frontid}_{self.sessionid}_{self.order_ref}"
        vt_orderid: str = f"{self.gateway_name}.{orderid}"

        return vt_orderid

    def query_account(self) -> None:
        """查询资金"""
        self.reqid += 1
        self.reqQryTradingAccount({}, self.reqid)

    def query_position(self) -> None:
        """查询持仓"""
        if not symbol_contract_map:
            return

        tts_req: dict = {
            "BrokerID": self.brokerid,
            "InvestorID": self.userid
        }

        self.reqid += 1
        self.reqQryInvestorPosition(tts_req, self.reqid)

    def close(self) -> None:
        """关闭连接"""
        if self.connect_status:
            self.exit()


class TqSdkMdApi:
    """
    TQSDK行情API类，用于从tqsdk订阅期货合约行情
    初始化和连接是分两步的
    初始化是在系统启动，创建对象时
    连接实在窗口调用连接时，这个和行情订阅有关
    """
    def __init__(self, gateway: TtstqGateway) -> None:
        """构造函数"""
        self.gateway: TtstqGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.api: TqApi | None = None
        self.active: bool = False
        self.thread: Thread | None = None
        # 在未连接前，还无法订阅行情，此处保存要订阅的数据，连接之后会再调用一次订阅
        self.subscribed: set = set()
        self.quotes: dict[str, Any] = {}  # 保存行情引用 {symbol: quote}

        # 测试下单相关
        self.order_placed: bool = False  # 是否已经下过单
        self.order_ids: list[str] = []  # 记录订单ID
        self.traded_vt_orderids: set = set()  # 已成交的订单ID集合

    def connect(self) -> None:
        """连接TQSDK"""
        try:
            # 初始化TQSDK API
            self.api = TqApi(auth=tq_auth)
            self.gateway.write_log("TQSDK行情连接成功")

            # 启动行情接收线程
            self.active = True
            self.thread = Thread(target=self._run)
            self.thread.start()
            self.gateway.write_log("TQSDK行情线程启动")
            for symbol in self.subscribed:
                self._subscribe_symbol(symbol)

            # 订阅ag2694
            self._subscribe_symbol('ag2604')
            self._subscribe_symbol('ag2606')

        except Exception as e:
            self.gateway.write_log(f"TQSDK行情连接失败：{str(e)}")

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        symbol: str = req.symbol

        # 过滤重复的订阅
        if symbol in self.subscribed:
            return
        # 订阅逻辑分已连接和未连接
        # 已连接情况，订阅合约，加入订阅列表
        # 未连接情况，只加入订阅列表，在连接后会再调用一次订阅
        if self.active:
            self._subscribe_symbol(req.symbol)
        self.subscribed.add(req.symbol)

    def _subscribe_symbol(self, symbol: str) -> None:
        """订阅行情"""
        # symbol: str = req.symbol

        # 过滤重复的订阅
        if symbol in self.quotes:
            self.gateway.write_log(f"{symbol}已添加订阅，无需再次订阅")
            return

        # 从全局合约映射中获取合约信息
        contract: ContractData | None = symbol_contract_map.get(symbol, None)
        if not contract:
            self.gateway.write_log(f"未找到合约{symbol}或行情未连接")
            return

        # 构造tqsdk格式的合约代码
        tq_symbol: str = f"{contract.exchange.value}.{symbol}"

        try:
            # 获取行情引用（自动订阅）
            quote = self.api.get_quote(tq_symbol)

            # 保存行情引用
            self.quotes[symbol] = quote
            self.gateway.write_log(f"TQSDK订阅行情成功：{tq_symbol}")

        except Exception as e:
            self.gateway.write_log(f"TQSDK订阅行情失败 [{tq_symbol}]：{str(e)}")

    def _run(self) -> None:
        """行情接收线程"""
        time_sync: bool = True
        while self.active:
            try:
                # 等待行情推送
                self.api.wait_update()

                near_quote: Quote = self.quotes['ag2604']
                far_quote: Quote = self.quotes['ag2606']

                # near_datetime_str格式：2025-12-17 22:28:03.500000
                # near_time_str格式：22:28:03.500000
                # 去除fartime秒后面的5个0（字符串切片：去掉最后5个字符） 结果："13:30:00.5"
                near_time_processed = str(near_quote.datetime)[:-5]
                far_time_processed = str(far_quote.datetime)[:-5]

                near_time = datetime.strptime(near_time_processed, '%Y-%m-%d %H:%M:%S.%f')
                far_time = datetime.strptime(far_time_processed, '%Y-%m-%d %H:%M:%S.%f')
                # 关键：带精度容错判断是否为0.5秒（避免浮点数精度问题）
                if abs(near_time - far_time) > timedelta(milliseconds=500):
                    # 相差0.5秒以上，不符合交易条件
                    time_sync = False
                else:
                    time_sync = True

                if near_quote.last_price and far_quote.last_price and time_sync:
                    self._spread_ag2604_ag2606(near_quote, far_quote)

                # 处理所有已订阅合约的行情
                for symbol, quote in self.quotes.items():
                    # 示例：移除值为偶数的键值对
                    # print(f"{local_time} symbol: {symbol} quote: {quote}")

                    if not quote.last_price or not quote.datetime:
                        self.gateway.write_log("quote数据无效")
                        continue

                    # 这个处理会再界面上显示实时的波动数据
                    self._process_tick(symbol, quote)
                    # 下单
                    # time_end = get_now()
                    # self.gateway.write_log(f"准备下单: {time_end} ")
                    #self._order_ag2604(symbol, quote)



            except Exception as e:
                if self.active:
                    self.gateway.write_log(f"TQSDK行情处理异常：{str(e)}")
                break

        # 退出循环后关闭API
        if self.api:
            try:
                self.api.close()
                self.gateway.write_log(f"TQSDK连接关闭")
            except Exception:
                self.gateway.write_log(f"TQSDK行情处理异常：{str(e)}")
            self.api = None

    def _spread_ag2604_ag2606(self, near_quote: Quote, far_quote: Quote) -> None:
        # 在这里做差价交易，差价为
        # ag_upper_band = 100
        # ag_middle_band = 35
        # ag_lower_band = -30
        # 交易策略：
        # 1、做空差价为 real_short_spread = near_quote.bid_price1 - far_quote.ask_price1
        # 2、做多差价为 real_long_spread = near_quote.ask_price1 - far_quote.bid_price1
        # 3、当real_short_spread高于ag_upper_band，卖出near_quote，买入far_quote
        # 4、当real_long_spread低于ag_lower_band，买入near_quote，卖出far_quote
        # 5、当持有做空差价的持仓时，real_long_spread达到ag_middle_band，就平仓
        # 6、当持有做多差价的持仓时，real_short_spread达到ag_middle_band，就平仓
        # 7、ag2604、ag2606的波动单位是1
        # 8、要考虑到滑点和交易手续费
        # 9、只成交一条腿的情况（实时监控，未成交的取消订单，成交的直接平仓）
        # 10、涨停跌停时，无法交易（大于涨停、跌停价格的80%，停止交易）
        # 11、交易手数变动暂时设置为1手，后期可调整
        # 12、下单使用市价成交OrderType.MARKET
        # 13、平仓使用平今Offset.CLOSETODAY，交易只在当天，当天交易最后5分钟，如果有持仓也平仓，最后5分钟不再交易。

        # 做空差价
        real_short_spread = near_quote.bid_price1 - far_quote.ask_price1
        # 做多差价
        real_long_spread = near_quote.ask_price1 - far_quote.bid_price1


    def _order_ag2604(self, symbol: str, quote: Quote):
        """测试ag2604下单功能"""
        # 只在ag2604上操作，且只下单一次
        if symbol != "ag2604" or self.order_placed:
            return

        # 标记已下单，避免重复下单
        self.order_placed = True

        try:
            # 获取合约信息
            contract: ContractData | None = symbol_contract_map.get(symbol, None)
            if not contract:
                self.gateway.write_log(f"未找到合约{symbol}的合约信息")
                return

            # 获取当前价格
            current_price = quote.last_price
            if current_price <= 0:
                self.gateway.write_log(f"获取{symbol}当前价格失败")
                return

            self.gateway.write_log(f"开始测试下单 {symbol}，当前价格: {current_price}")

            # 下单1：买单（开仓）
            req_buy: OrderRequest = OrderRequest(
                symbol=symbol,
                exchange=contract.exchange,
                direction=Direction.LONG,
                type=OrderType.MARKET,
                volume=1,
                price=0,  # 市价单价格设为0
                offset=Offset.OPEN,
                reference="test_buy"
            )
            time_begin = get_now()
            order_id_buy: str = self.gateway.send_order(req_buy)
            time_end = get_now()
            self.gateway.write_log(f"下单时间:{time_begin} - {time_end} ")

            if order_id_buy:
                self.order_ids.append(order_id_buy)
                self.gateway.write_log(f"买单已发送: {order_id_buy}")
            else:
                self.gateway.write_log("买单发送失败")
                return

            # 下单2：卖单（开仓）
            req_sell: OrderRequest = OrderRequest(
                symbol=symbol,
                exchange=contract.exchange,
                direction=Direction.SHORT,
                type=OrderType.MARKET,
                volume=1,
                price=0,  # 市价单价格设为0
                offset=Offset.OPEN,
                reference="test_sell"
            )
            time_begin = get_now()
            order_id_sell: str = self.gateway.send_order(req_sell)
            time_end = get_now()
            self.gateway.write_log(f"下单时间:{time_begin} - {time_end}")
            if order_id_sell:
                self.order_ids.append(order_id_sell)
                self.gateway.write_log(f"卖单已发送: {order_id_sell}")
            else:
                self.gateway.write_log("卖单发送失败")
                return

            # 启动定时器，10秒后平仓
            timer = Thread(target=self._delay_close_positions, args=(symbol, contract,))
            timer.daemon = True
            timer.start()
            self.gateway.write_log("已启动10秒定时器，届时将自动平仓")

        except Exception as e:
            self.gateway.write_log(f"下单异常: {str(e)}")

    def _delay_close_positions(self, symbol: str, contract: ContractData):
        """延迟平仓的线程函数"""
        sleep(10)  # 等待10秒
        self.gateway.write_log("10秒已到，开始平仓...")
        self._close_ag2604_positions(symbol, contract)

    def _close_ag2604_positions(self, symbol: str, contract: ContractData):
        """智能平仓ag2604的持仓，区分上期所今仓昨仓"""
        try:
            # 先查询持仓，获取持仓详情
            self.gateway.query_position()


            # 这里有update_pos_condition同步问题，所以加锁
            with self.gateway.update_pos_condition:
                self.gateway.update_pos_condition.wait()

                # 从gateway的td_api获取持仓数据
                td_api = self.gateway.td_api

                # 获取多单持仓（Direction.LONG）
                long_positions = [pos for pos in td_api.positions_for_tqsdk.values()
                                if pos.symbol == symbol and pos.direction == Direction.LONG and pos.volume > 0]

                # 获取空单持仓（Direction.SHORT）
                short_positions = [pos for pos in td_api.positions_for_tqsdk.values()
                                 if pos.symbol == symbol and pos.direction == Direction.SHORT and pos.volume > 0]
                # 临时变量，用完即刻清空
                td_api.positions_for_tqsdk.clear()

            # 平多单
            for pos in long_positions:
                close_volume = pos.volume
                if close_volume <= 0:
                    continue

                # 判断平仓offset：上期所需要区分今昨仓
                if contract.exchange in [Exchange.SHFE, Exchange.INE]:
                    # 如果有昨仓，先平昨仓；剩下的平今仓
                    if pos.yd_volume > 0:
                        close_yd_volume = min(pos.yd_volume, close_volume)
                        self._send_close_order(symbol, contract, Direction.SHORT,
                                              close_yd_volume, Offset.CLOSEYESTERDAY)
                        close_volume -= close_yd_volume

                    if close_volume > 0:
                        self._send_close_order(symbol, contract, Direction.SHORT,
                                              close_volume, Offset.CLOSETODAY)
                else:
                    # 非上期所，直接平仓
                    self._send_close_order(symbol, contract, Direction.SHORT,
                                          close_volume, Offset.CLOSE)

            # 平空单
            for pos in short_positions:
                close_volume = pos.volume
                if close_volume <= 0:
                    continue

                # 判断平仓offset：上期所需要区分今昨仓
                if contract.exchange in [Exchange.SHFE, Exchange.INE]:
                    # 如果有昨仓，先平昨仓；剩下的平今仓
                    if pos.yd_volume > 0:
                        close_yd_volume = min(pos.yd_volume, close_volume)
                        self._send_close_order(symbol, contract, Direction.LONG,
                                              close_yd_volume, Offset.CLOSEYESTERDAY)
                        close_volume -= close_yd_volume

                    if close_volume > 0:
                        self._send_close_order(symbol, contract, Direction.LONG,
                                              close_volume, Offset.CLOSETODAY)
                else:
                    # 非上期所，直接平仓
                    self._send_close_order(symbol, contract, Direction.LONG,
                                          close_volume, Offset.CLOSE)

            # 清空订单记录
            self.order_ids.clear()

        except Exception as e:
            self.gateway.write_log(f"平仓异常: {str(e)}")

    def _send_close_order(self, symbol: str, contract: ContractData,
                         direction: Direction, volume: int, offset: Offset):
        """发送平仓订单"""
        req: OrderRequest = OrderRequest(
            symbol=symbol,
            exchange=contract.exchange,
            direction=direction,
            type=OrderType.MARKET,
            volume=volume,
            price=0,
            offset=offset,
            reference=f"test_close_{offset.value}"
        )
        order_id: str = self.gateway.send_order(req)
        if order_id:
            self.gateway.write_log(f"平仓单已发送 [{direction.value} {offset.value} {volume}手]: {order_id}")
        else:
            self.gateway.write_log(f"平仓单发送失败 [{direction.value} {offset.value} {volume}手]")
    def _process_tick(self, symbol: str, quote: Quote) -> None:
        """处理单个合约的行情数据"""
        if not self.api:
            return

        try:
            # 从保存的行情引用中获取quote
            # quote = self.quotes.get(symbol)
            # if not quote:
            #     return

            # # 使用is_changing检查行情是否有变化
            # if not self.api.is_changing(quote):
            #     return

            # 检查行情数据是否有效
            if not quote.datetime:
                return

            # # 从全局合约映射中获取合约信息
            # contract: ContractData | None = symbol_contract_map.get(symbol, None)
            # if not contract:
            #     return
            #
            # # 转换时间为vnpy格式
            # dt: datetime = datetime.fromtimestamp(quote.datetime / 1e9, tz=CHINA_TZ)

            exchange, _, symbol = quote.instrument_id.partition('.')

            dt = datetime_format(quote.datetime)

            # 构造TickData对象
            tick: TickData = TickData(
                symbol=symbol,
                exchange=EXCHANGE_TTS2VT[exchange],
                datetime=dt,
                name=quote.instrument_name,
                volume=quote.volume,
                turnover=quote.amount,
                open_interest=quote.open_interest,
                last_price=adjust_price(quote.last_price),
                limit_up=quote.upper_limit,
                limit_down=quote.lower_limit,
                open_price=adjust_price(quote.open),
                high_price=adjust_price(quote.highest),
                low_price=adjust_price(quote.lowest),
                pre_close=adjust_price(quote.pre_close),
                bid_price_1=adjust_price(quote.bid_price1),
                ask_price_1=adjust_price(quote.ask_price1),
                bid_volume_1=quote.bid_volume1,
                ask_volume_1=quote.ask_volume1,
                gateway_name=self.gateway_name
            )

            # 如果有五档行情，也设置
            if quote.bid_volume2 or quote.ask_volume2:
                tick.bid_price_2 = adjust_price(quote.bid_price2)
                tick.bid_price_3 = adjust_price(quote.bid_price3)
                tick.bid_price_4 = adjust_price(quote.bid_price4)
                tick.bid_price_5 = adjust_price(quote.bid_price5)

                tick.ask_price_2 = adjust_price(quote.ask_price2)
                tick.ask_price_3 = adjust_price(quote.ask_price3)
                tick.ask_price_4 = adjust_price(quote.ask_price4)
                tick.ask_price_5 = adjust_price(quote.ask_price5)

                tick.bid_volume_2 = quote.bid_volume2
                tick.bid_volume_3 = quote.bid_volume3
                tick.bid_volume_4 = quote.bid_volume4
                tick.bid_volume_5 = quote.bid_volume5

                tick.ask_volume_2 = quote.ask_volume2
                tick.ask_volume_3 = quote.ask_volume3
                tick.ask_volume_4 = quote.ask_volume4
                tick.ask_volume_5 = quote.ask_volume5

            # 推送行情数据
            self.gateway.on_tick(tick)

        except Exception as e:
            self.gateway.write_log(f"TQSDK行情数据转换异常 [{symbol}]：{str(e)}")



    def close(self) -> None:
        """关闭连接"""
        self.active = False

        # 等待线程结束
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
            self.thread = None

        # 关闭API
        if self.api:
            try:
                self.api.close()
            except Exception:
                pass
            self.api = None

        # 清空数据
        self.quotes.clear()
        self.gateway.write_log("TQSDK行情连接已关闭")


def adjust_price(price: float) -> float:
    """将异常的浮点数最大值（MAX_FLOAT）数据调整为0"""
    if price == MAX_FLOAT:
        price = 0
    return price

def check_tqsdk(market_source: str):
    # 根据配置选择行情源
    if market_source == "TQSDK" and TQSDK_AVAILABLE:
        return "TQSDK"
    else:  # 默认使用TTS行情
        return "TTS"

# tick: TickData = TickData(
            #     symbol=symbol,
            #     exchange=exchange,
            #     datetime=dt,
            #     name=quote.instrument_name,
            #     volume=quote.volume if hasattr(quote, 'volume') else 0,
            #     turnover=quote.turnover if hasattr(quote, 'turnover') else 0,
            #     open_interest=quote.open_interest if hasattr(quote, 'open_interest') else 0,
            #     last_price=adjust_price(quote.last_price),
            #     limit_up=quote.upper_limit if hasattr(quote, 'upper_limit') else 0,
            #     limit_down=quote.lower_limit if hasattr(quote, 'lower_limit') else 0,
            #     open_price=adjust_price(quote.open_price) if hasattr(quote, 'open_price') else 0,
            #     high_price=adjust_price(quote.highest_price) if hasattr(quote, 'highest_price') else 0,
            #     low_price=adjust_price(quote.lowest_price) if hasattr(quote, 'lowest_price') else 0,
            #     pre_close=adjust_price(quote.pre_close) if hasattr(quote, 'pre_close') else 0,
            #     bid_price_1=adjust_price(quote.bid_price1) if hasattr(quote, 'bid_price1') else 0,
            #     ask_price_1=adjust_price(quote.ask_price1) if hasattr(quote, 'ask_price1') else 0,
            #     bid_volume_1=quote.bid_volume1 if hasattr(quote, 'bid_volume1') else 0,
            #     ask_volume_1=quote.ask_volume1 if hasattr(quote, 'ask_volume1') else 0,
            #     gateway_name=self.gateway_name
            # )

# if hasattr(quote, 'bid_price2') and quote.bid_price2 != 0:
#     tick.bid_price_2 = adjust_price(quote.bid_price2)
#     tick.bid_price_3 = adjust_price(quote.bid_price3) if hasattr(quote, 'bid_price3') else 0
#     tick.bid_price_4 = adjust_price(quote.bid_price4) if hasattr(quote, 'bid_price4') else 0
#     tick.bid_price_5 = adjust_price(quote.bid_price5) if hasattr(quote, 'bid_price5') else 0
#
#     tick.ask_price_2 = adjust_price(quote.ask_price2)
#     tick.ask_price_3 = adjust_price(quote.ask_price3) if hasattr(quote, 'ask_price3') else 0
#     tick.ask_price_4 = adjust_price(quote.ask_price4) if hasattr(quote, 'ask_price4') else 0
#     tick.ask_price_5 = adjust_price(quote.ask_price5) if hasattr(quote, 'ask_price5') else 0
#
#     tick.bid_volume_2 = quote.bid_volume2 if hasattr(quote, 'bid_volume2') else 0
#     tick.bid_volume_3 = quote.bid_volume3 if hasattr(quote, 'bid_volume3') else 0
#     tick.bid_volume_4 = quote.bid_volume4 if hasattr(quote, 'bid_volume4') else 0
#     tick.bid_volume_5 = quote.bid_volume5 if hasattr(quote, 'bid_volume5') else 0
#
#     tick.ask_volume_2 = quote.ask_volume2 if hasattr(quote, 'ask_volume2') else 0
#     tick.ask_volume_3 = quote.ask_volume3 if hasattr(quote, 'ask_volume3') else 0
#     tick.ask_volume_4 = quote.ask_volume4 if hasattr(quote, 'ask_volume4') else 0
#     tick.ask_volume_5 = quote.ask_volume5 if hasattr(quote, 'ask_volume5') else 0