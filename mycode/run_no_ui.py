import multiprocessing
import sys
from time import sleep
from datetime import datetime, time

from vnpy.event import EventEngine
from vnpy.trader.setting import SETTINGS
from vnpy.trader.engine import MainEngine, LogEngine
from vnpy.trader.logger import INFO, logger


# from vnpy_ctastrategy import CtaStrategyApp, CtaEngine
# from vnpy_ctastrategy.base import EVENT_CTA_LOG
CONN='TTS'
# 这三个只能留一个，不然会报错
if CONN == 'CTP':
    from vnpy_ctp import CtpGateway
elif CONN == 'CTPTQ':
    from vnpy_ctptq import CtptqGateway
elif CONN == 'CTPTEST':
    from vnpy_ctptest import CtptestGateway
elif CONN == 'CTPTESTTQ':
    from vnpy_ctptesttq import CtptesttqGateway
elif CONN == 'TTS':
    from vnpy_tts import TtsGateway
else:
    print('请选择正确的连接方式：CTP | CTPTQ | CTP_TEST | CTPTESTTQ | TTS')
    exit(0)

SETTINGS["log.active"] = True
SETTINGS["log.level"] = INFO
SETTINGS["log.console"] = True


ctp_setting = {
    "用户名": "",
    "密码": "",
    "经纪商代码": "",
    "交易服务器": "",
    "行情服务器": "",
    "产品名称": "",
    "授权编码": "",
    "产品信息": ""
}
tss_setting = {
    "用户名": "16788",
    "密码": "123456",
    "经纪商代码": "9999",
    "交易服务器": "tcp://trading.openctp.cn:30001",
    "行情服务器": "tcp://trading.openctp.cn:30011",
    "产品名称": "",
    "授权编码": ""
}

# Chinese futures market trading period (day/night)
DAY_START = time(8, 45)
DAY_END = time(15, 0)

NIGHT_START = time(20, 45)
NIGHT_END = time(2, 45)


def check_trading_period() -> bool:
    return True
    """"""
    current_time = datetime.now().time()

    trading = False
    if (
        (current_time >= DAY_START and current_time <= DAY_END)
        or (current_time >= NIGHT_START)
        or (current_time <= NIGHT_END)
    ):
        trading = True

    return trading


def run_child() -> None:
    """
    Running in the child process.
    """
    SETTINGS["log.file"] = True

    event_engine: EventEngine = EventEngine()
    main_engine: MainEngine = MainEngine(event_engine)


    # 这三个只能留一个，不然会报错
    if CONN == 'CTP':
        main_engine.add_gateway(CtpGateway)
        main_engine.connect(ctp_setting, CONN)
    elif CONN == 'CTPTQ':
        main_engine.add_gateway(CtptqGateway)
    elif CONN == 'CTPTEST':
        main_engine.add_gateway(CtptestGateway)
    elif CONN == 'CTPTESTTQ':
        main_engine.add_gateway(CtptesttqGateway)
    elif CONN == 'TTS':
        main_engine.add_gateway(TtsGateway)
        main_engine.connect(tss_setting, CONN)
    else:
        exit(0)

    # cta_engine: CtaEngine = main_engine.add_app(CtaStrategyApp)
    # logger.info("主引擎创建成功")

    # log_engine: LogEngine = main_engine.get_engine("log")
    # event_engine.register(EVENT_CTA_LOG, log_engine.process_log_event)
    # logger.info("注册日志事件监听")

    logger.info("连接CTP接口")

    sleep(10)

    # cta_engine.init_engine()
    # logger.info("CTA策略初始化完成")
    #
    # cta_engine.init_all_strategies()
    # sleep(60)   # Leave enough time to complete strategy initialization
    # logger.info("CTA策略全部初始化")
    #
    # cta_engine.start_all_strategies()
    # logger.info("CTA策略全部启动")

    while True:
        sleep(10)

        trading = check_trading_period()
        if not trading:
            logger.info("关闭子进程")
            main_engine.close()
            sys.exit(0)


def run_parent() -> None:
    """
    Running in the parent process.
    """
    print("启动守护父进程")

    child_process = None

    while True:
        trading = check_trading_period()

        # Start child process in trading period
        if trading and child_process is None:
            print("启动子进程")
            child_process = multiprocessing.Process(target=run_child)
            child_process.start()
            print("子进程启动成功")

        # 非记录时间则退出子进程
        if not trading and child_process is not None:
            if not child_process.is_alive():
                child_process = None
                print("子进程关闭成功")

        sleep(5)


if __name__ == "__main__":
    run_parent()
