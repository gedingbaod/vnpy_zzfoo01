from datetime import datetime, timedelta, time, timezone

def check_trade_time(now_str, trade_str, volume):
    """
    解析时间字符串并根据规则判断

    参数:
        time_str: 时间字符串，格式为 "YYYY-MM-DD HH:MM:SS+TZ:TZ"

    返回:
        1 或 0
    """
    # 解析时间字符串
    now = datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S")
    trade_time = datetime.fromisoformat(trade_str)

    # 移除trade_time的时区信息，便于比较
    trade_datetime = trade_time.replace(tzinfo=None)

    # 规则1: 如果trade_time比当前时间小24小时，返回1
    if trade_datetime < now - timedelta(hours=24):
        return volume

    # 获取当前时间的时间部分
    now_time_only = now.time()

    # 规则2: 如果当前时间大于15点，trade_time小于当天的15点，返回1
    if now_time_only > time(15, 0):
        # 获取trade_time那天的15点
        trade_day_15 = trade_datetime.replace(hour=15, minute=0, second=0, microsecond=0)
        if trade_datetime < trade_day_15:
            return volume

    # 规则3: 如果当前时间在0点到15点之间，trade_time小于前日21点，返回1
    if time(0, 0) <= now_time_only <= time(15, 0):
        # 计算前日21点的时间
        prev_day_21 = (now - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
        if trade_datetime < prev_day_21:
            return volume

    # 其余情况返回0
    return 0


if __name__ == "__main__":
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 测试用例：当前时间
    print("-" * 60)
    # now_time = "2026-03-30 12:48:07"
    trade_time_str = "2026-03-27 09:02:08+08:00"  # 默认测试
    print(f"\ntrade_time={trade_time_str}, now={now_time}")
    result = check_trade_time(now_time, trade_time_str)
    print(f"结果: {result}")

    # 测试用例：当前时间
    print("-" * 60)
    now_time = "2026-03-30 22:48:07"
    trade_time_str = "2026-03-30 14:00:00+08:00"  # 今天14点
    print(f"\ntrade_time={trade_time_str}, now={now_time}")
    result = check_trade_time(now_time, trade_time_str)
    print(f"结果: {result}")

    # 测试用例：当前时间
    print("-" * 60)
    now_time = "2026-03-30 08:48:07"
    trade_time_str = "2026-03-29 21:01:00+08:00"  # 今天16点
    print(f"\ntrade_time={trade_time_str}, now={now_time}")
    result = check_trade_time(now_time, trade_time_str)
    print(f"结果: {result}")

    # 测试用例：当前时间
    print("-" * 60)
    now_time = "2026-03-30 08:48:07"
    trade_time_str = "2026-03-29 14:00:00+08:00"  # 昨天晚上22点
    print(f"\ntrade_time={trade_time_str}, now={now_time}")
    result = check_trade_time(now_time, trade_time_str)
    print(f"结果: {result}")
