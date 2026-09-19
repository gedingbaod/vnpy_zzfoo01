
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp


CONN='CTP'
# 这六个只能留一个，不然会报错
if CONN == 'CTP':
    from vnpy_ctp import CtpGateway
elif CONN == 'CTPTQ':
    from vnpy_ctptq import CtptqGateway
elif CONN == 'CTPTQTTS':
    from vnpy_ctptqtts import CtptqttsGateway
elif CONN == 'CTPTEST':
    from vnpy_ctptest import CtptestGateway
elif CONN == 'CTPTESTTQ':
    from vnpy_ctptesttq import CtptesttqGateway
elif CONN == 'TTS':
    from vnpy_tts import TtsGateway
elif CONN == 'TTSTQ':
    from vnpy_ttstq import TtstqGateway
else:
    print('请选择正确的连接方式：CTP | CTPTQ | CTP_TEST | CTPTESTTQ | TTS | TTSTQ')
    exit(0)

# from vnpy_mini import MiniGateway
# from vnpy_femas import FemasGateway
# from vnpy_sopt import SoptGateway
# from vnpy_uft import UftGateway
# from vnpy_esunny import EsunnyGateway
# from vnpy_xtp import XtpGateway
# from vnpy_tora import ToraStockGateway, ToraOptionGateway
# from vnpy_ib import IbGateway
# from vnpy_tap import TapGateway
# from vnpy_da import DaGateway
# from vnpy_rohon import RohonGateway


# from vnpy_paperaccount import PaperAccountApp
from vnpy_ctastrategy import CtaStrategyApp
# from vnpy_ctabacktester import CtaBacktesterApp
# from vnpy_spreadtrading import SpreadTradingApp
# from vnpy_algotrading import AlgoTradingApp
# from vnpy_optionmaster import OptionMasterApp
# from vnpy_portfoliostrategy import PortfolioStrategyApp
# from vnpy_scripttrader import ScriptTraderApp
# from vnpy_chartwizard import ChartWizardApp
# from vnpy_rpcservice import RpcServiceApp
# from vnpy_excelrtd import ExcelRtdApp
# from vnpy_datamanager import DataManagerApp
# from vnpy_datarecorder import DataRecorderApp
from vnpy_riskmanager import RiskManagerApp
# from vnpy_webtrader import WebTraderApp
# from vnpy_portfoliomanager import PortfolioManagerApp


def main():
    """"""
    qapp = create_qapp()

    event_engine = EventEngine()

    main_engine = MainEngine(event_engine)

    # 这六个只能留一个，不然会报错
    if CONN == 'CTP':
        main_engine.add_gateway(CtpGateway)
    elif CONN == 'CTPTQ':
        main_engine.add_gateway(CtptqGateway, gateway_name=CONN)
    elif CONN == 'CTPTQTTS':
        main_engine.add_gateway(CtptqttsGateway, gateway_name=CONN)
    elif CONN == 'CTPTEST':
        main_engine.add_gateway(CtptestGateway, gateway_name=CONN)
    elif CONN == 'CTPTESTTQ':
        main_engine.add_gateway(CtptesttqGateway, gateway_name=CONN)
    elif CONN == 'TTS':
        main_engine.add_gateway(TtsGateway, gateway_name=CONN)
    elif CONN == 'TTSTQ':
        main_engine.add_gateway(TtstqGateway, gateway_name=CONN)
    else:
        exit(0)
    # main_engine.add_gateway(MiniGateway)
    # main_engine.add_gateway(FemasGateway)
    # main_engine.add_gateway(SoptGateway)
    # main_engine.add_gateway(UftGateway)
    # main_engine.add_gateway(EsunnyGateway)
    # main_engine.add_gateway(XtpGateway)
    # main_engine.add_gateway(ToraStockGateway)
    # main_engine.add_gateway(ToraOptionGateway)
    # main_engine.add_gateway(IbGateway)
    # main_engine.add_gateway(TapGateway)
    # main_engine.add_gateway(DaGateway)
    # main_engine.add_gateway(RohonGateway)
    # main_engine.add_gateway(TtsGateway)

    # main_engine.add_app(PaperAccountApp)
    main_engine.add_app(CtaStrategyApp)
    # main_engine.add_app(CtaBacktesterApp)
    # main_engine.add_app(SpreadTradingApp)
    # main_engine.add_app(AlgoTradingApp)
    # main_engine.add_app(OptionMasterApp)
    # main_engine.add_app(PortfolioStrategyApp)
    # main_engine.add_app(ScriptTraderApp)
    # main_engine.add_app(ChartWizardApp)
    # main_engine.add_app(RpcServiceApp)
    # main_engine.add_app(ExcelRtdApp)
    # main_engine.add_app(DataManagerApp)
    # main_engine.add_app(DataRecorderApp)
    main_engine.add_app(RiskManagerApp)
    # main_engine.add_app(WebTraderApp)
    # main_engine.add_app(PortfolioManagerApp)

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
