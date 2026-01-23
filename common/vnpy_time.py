import time
from datetime import datetime

def get_now():
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

if __name__ == '__main__':
    print(get_now())
    print(date_format_shorten('2026-01-22 22:06:51.000000'))
    print(datetime_format('2026-01-22 22:06:51.501002'))