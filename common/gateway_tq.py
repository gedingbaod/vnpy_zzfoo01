from threading import Condition
from typing import Union

# from common.tqsdk_gateway import TqSdkMdApi
from vnpy.event import EventEngine
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import PositionData, ContractData

try:
    from tqsdk import TqApi, TqAuth
    TQSDK_AVAILABLE = True
except ImportError:
    TQSDK_AVAILABLE = False

MARKET_SOURCE_TQSDK = "TQSDK"
MARKET_SOURCE_TTS = "TTS"

class BaseGatewayTq(BaseGateway):

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.tqsdk_available = TQSDK_AVAILABLE          # 通过导入检查tqsdk是否导入
        self.market_source: str = MARKET_SOURCE_TTS   # 区分行情源是TTS还是TTSTQ，默认使用TTS行情，再连接以前不要改变原有逻辑
        self.tq_md_api = None  # TQSDK行情API，这里定义TqSdkMdApi，会产生循环依赖

        self.positions_for_tqsdk: dict[str, PositionData] = {}  # 仓位数据，从TdApi的onRspQryInvestorPosition中更新
        self.update_pos_condition: Condition = Condition()  # 用于更新仓位时，做同步处理

        # 合约数据全局缓存字典，会在TdApi的onRspQryInstrument中更新
        self.update_map_condition: Condition = Condition()  # 用于更新合约时，做同步处理
        self.symbol_contract_map_tqsdk: dict[str, ContractData] = {}  # 一定要用TdApi的合约是因为发送交易也是到这里


        # TdApi的调用链：
