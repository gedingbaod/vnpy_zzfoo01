import time
from typing import Callable

# 常量：一天的总纳秒数（24*3600*10^9）
ONE_DAY_NS = 86400 * 10 ** 9
# 用来卡时间的常量，用来和time.time_ns()做比较
# 这个是当日的时间偏移量，使用时要先用ONE_DAY_NS整除
# 注意这个是Unix时间，是没有加时区的
# 早盘，不带时区就是00:59:00,00:59:59.0
NS_005900 = 3540000000000
NS_005959 = 3599900000000
# 夜盘，不带时区就是12:59:00,12:59:59.0
NS_125900 = 46740000000000
NS_125959 = 46799900000000

NS_NOTE_BOOK_BIAS = 161200 * (10 ** 3)
# NS_NOTE_BOOK_BIAS = 200000 * (10 ** 3)

def check_market_opening_time_morning() -> bool:
    """
    极致高性能判断：仅3步（获取纳秒戳 + 两次数值比较）
    单次调用耗时 ≈ 20纳秒（仅time.time_ns()的系统调用开销）
    """
    current_ns = time.time_ns()
    current_ns_today = current_ns % ONE_DAY_NS
    # 直接比较纳秒戳（数值比较是CPU原生操作，无任何开销）
    return NS_005900 < current_ns_today < NS_005959

def check_market_opening_time_night() -> bool:
    """
    极致高性能判断：仅3步（获取纳秒戳 + 两次数值比较）
    单次调用耗时 ≈ 20纳秒（仅time.time_ns()的系统调用开销）
    """
    current_ns = time.time_ns()
    current_ns_today = current_ns % ONE_DAY_NS
    # 直接比较纳秒戳（数值比较是CPU原生操作，无任何开销）
    return NS_125900 < current_ns_today < NS_125959

def parse_time_str_to_ns(time_str: str) -> int:
    """
    解析 "HH:MM:SS.NNNNNNNNN" 格式的时间字符串为「当天0点起的纳秒数」
    （无datetime依赖，纯数值解析）
    :param time_str: 时间字符串，如 "09:00:00.000001"（9点整1微秒=1000纳秒）
    :return: 从当天0点到指定时间的总纳秒数
    """
    # 拆分时分秒和纳秒部分
    if "." in time_str:
        hms_part, ns_part = time_str.split(".")
        # 补全纳秒到9位（不足补0，超出截断）
        ns_part = ns_part.ljust(9, "0")[:9]
        ns = int(ns_part)
    else:
        hms_part = time_str
        ns = 0

    # 拆分时分秒（纯数值解析）
    h, m, s = map(int, hms_part.split(":"))

    # 计算总纳秒数：时→秒→纳秒 + 分→秒→纳秒 + 秒→纳秒 + 纳秒
    total_ns = (h * 3600 + m * 60 + s) * 10 ** 9 + ns
    return total_ns


def get_today_zero_ns() -> int:
    """
    获取「当天0点整」的Unix纳秒级时间戳（无datetime依赖）
    :return: 当天0点的Unix纳秒戳
    """
    # 获取当前时间的秒级Unix时间戳
    now_s = time.time()
    # 转换为本地时间结构体（轻量级，无datetime开销）
    local_t = time.localtime(now_s)
    # 计算当天0点的秒数：当前天的秒数 - 已过的秒数
    today_zero_s = now_s - (local_t.tm_hour * 3600 + local_t.tm_min * 60 + local_t.tm_sec)
    # 转为纳秒戳（舍去小数部分，保证0点整）
    return int(today_zero_s) * 10 ** 9


def format_ns_to_readable(ns: int) -> tuple[str, str]:
    """
    将Unix纳秒戳格式化为「可读时间字符串」
    :param ns: Unix纳秒级时间戳
    :return: (微秒级可读字符串, 纳秒级可读字符串)
             示例：("09:00:00.000001", "09:00:00.000001234")
    """
    # 拆分秒和纳秒
    s = ns // 10 ** 9
    remain_ns = ns % 10 ** 9
    # 轻量级解析本地时间
    local_t = time.localtime(s)
    # 格式化时分秒（纯数值拼接，无strftime高开销）
    hhmmss = f"{local_t.tm_hour:02d}:{local_t.tm_min:02d}:{local_t.tm_sec:02d}"
    # 微秒级（6位）：纳秒//1000
    micro_str = f"{hhmmss}.{remain_ns // 1000:06d}"
    # 纳秒级（9位）
    ns_str = f"{hhmmss}.{remain_ns:09d}"
    return micro_str, ns_str


def precise_time_trigger(target_time_str: str, callback: Callable, *args, **kwargs):
    """
    纯time模块实现的纳秒级时间触发函数（无datetime依赖）
    :param target_time_str: 触发时间字符串，如 "09:00:00.000001"
    :param callback: 触发时调用的函数
    :param args: 回调函数位置参数
    :param kwargs: 回调函数关键字参数
    """
    # 1. 解析目标时间为「当天0点起的纳秒数」
    target_today_ns = parse_time_str_to_ns(target_time_str)
    # 2. 获取当天0点的Unix纳秒戳
    today_zero_ns = get_today_zero_ns()
    # 3. 计算目标时间的Unix纳秒戳
    target_abs_ns = today_zero_ns + target_today_ns + NS_NOTE_BOOK_BIAS
    # 4. 获取当前Unix纳秒戳
    current_abs_ns = time.time_ns()

    # 5. 计算需要等待的纳秒数（跨天处理）
    wait_ns = target_abs_ns - current_abs_ns
    if wait_ns < 0:
        wait_ns += 24 * 3600 * (10 ** 9)  # 加一天的纳秒数
        print(f"目标时间已过当天，将等待到次日 {target_time_str} 触发")

    # 6. 高精度等待（纳秒级，避免系统时间修改影响）
    start_perf_ns = time.perf_counter_ns()
    print(f"开始等待 {wait_ns / 10 ** 9:.9f} 秒（{wait_ns} 纳秒），目标触发时间：{target_time_str}")

    # 空循环等待（CPU略高但精度最高；允许毫秒误差可加 time.sleep(0.001)）
    while time.perf_counter_ns() - start_perf_ns < wait_ns:
        pass

    # 7. 触发回调函数（记录触发时的精准时间）
    trigger_abs_ns = time.time_ns()
    trigger_perf_ns = time.perf_counter_ns()
    micro_str, ns_str = format_ns_to_readable(trigger_abs_ns)

    print(f"✅ 到达目标时间！触发时Unix纳秒戳：{trigger_abs_ns}")
    callback(*args, **kwargs)


# ==================== 优化后的回调函数（无datetime） ====================
def my_callback(trigger_abs_ns: int, trigger_perf_ns: int, micro_str: str, ns_str: str, msg: str):
    """
    触发回调函数（纯time模块实现，无datetime依赖）
    :param trigger_abs_ns: 触发时的Unix纳秒戳
    :param trigger_perf_ns: 触发时的系统高精度计数器值
    :param micro_str: 触发时间的微秒级可读字符串
    :param ns_str: 触发时间的纳秒级可读字符串
    :param msg: 自定义消息
    """
    print(f"\n📢 回调函数执行！")
    print(f"   触发时间（微秒级）：{micro_str}")
    print(f"   触发时间（纳秒级）：{ns_str}")
    print(f"   触发时Unix纳秒戳：{trigger_abs_ns}")
    print(f"   系统高精度计数器：{trigger_perf_ns}")
    print(f"   消息：{msg}")


# ==================== 测试示例 ====================
if __name__ == "__main__":
    # 测试：指定触发时间（建议先改为当前时间+几秒，如 "16:30:00.000001"）
    # precise_time_trigger(
    #     target_time_str="08:51:10.000001",  # 目标时间：9点整1微秒
    #     callback=my_callback,
    #     msg="纳秒级触发成功！（无datetime依赖）"
    # )


    start = time.time()
    for _ in range(10000):
        check_market_opening_time_morning()
        check_market_opening_time_night()
    elapsed = time.time() - start

    print(elapsed)