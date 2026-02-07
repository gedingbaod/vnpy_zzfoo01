import time
from datetime import datetime

def get_now_str():
    ns = time.time_ns()
    seconds_total = ns // 1_000_000_000
    ms_part = (ns % 1_000_000_000) // 1_000
    # 将总秒数转换为时分秒
    secs_today = seconds_total % 86400
    hours = secs_today // 3600
    # 加时区
    hours = (hours + 8) % 24
    minutes = (secs_today % 3600) // 60
    seconds = secs_today % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms_part:06d}"

def get_now_micro():
    ns = time.time_ns()
    micro_seconds_total = ns // 1_000_000
    return micro_seconds_total

def get_now_1000ns():
    ns = time.time_ns()
    micro_seconds_total = ns // 1_000
    return micro_seconds_total

def get_now_ns():
    return time.time_ns()

# 完全替代 datetime.now().timestamp()，性能提升8~10倍
def get_timestamp():
    return time.time()

def date_format_shorten(dt_str: str) -> str:
    """最高性能：直接切片"""
    return dt_str[0:4] + dt_str[5:7] + dt_str[8:10]

def datetime_format(dt_str):
    return datetime(
        int(dt_str[:4]),
        int(dt_str[5:7]),
        int(dt_str[8:10]),
        int(dt_str[11:13]),
        int(dt_str[14:16]),
        int(dt_str[17:19]),
        int(int(dt_str[20:26]) // 1000 * 1000),
        )


def split_cross_day_time(time_arr):
    """
    拆分跨天的时间段数组（如21:00:00-26:30:00拆分为21:00:00-23:59:59和00:00:00-02:30:00）
    :param time_arr: 输入数组，格式为 [[start_time, end_time]]，时间格式HH:MM:SS
    :return: 拆分后的时间段数组
    """
    # 提取原始开始和结束时间
    start_time = time_arr[0][0]
    end_time = time_arr[0][1]

    # 解析结束时间的小时、分钟、秒（处理26:30:00这类超过24小时的时间）
    end_h, end_m, end_s = map(int, end_time.replace('.', ':').split(':'))  # 兼容02:30.00这种格式
    # 计算跨天后的实际小时数（26-24=2）
    actual_end_h = end_h - 24
    # 构造跨天的两段时间
    segment1 = [start_time, '23:59:59']  # 当天段：21:00:00-23:59:59
    # 次日段：00:00:00-实际结束时间（如02:30:00）
    segment2 = [
        '00:00:00',
        f"{actual_end_h:02d}:{end_m:02d}:{end_s:02d}"  # 补零保证格式统一（如2→02）
    ]

    return [segment1, segment2]




if __name__ == '__main__':
    print(get_now_str())
    print(date_format_shorten('2026-01-22 22:06:51.000000'))
    print(datetime_format('2026-01-22 22:06:51.501002'))

    # 测试你的场景
    original_arr = [['21:00:00', '26:30:00']]
    split_arr = split_cross_day_time(original_arr)
    print("拆分后的数组：", split_arr)

    print(datetime.now().timestamp())
    print(get_now_micro())
    print(get_now_ns())
    a = get_timestamp()
    b = datetime.now().timestamp()
    print(type(a))
    print(type(b))
    print(a)
    print(b)
    print(a > b)