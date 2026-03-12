"""
从 DolphinDB 获取每日 9:00 到 9:05 的 tick 数据并画图展示
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from vnpy.trader.constant import Exchange
from vnpy.trader.setting import SETTINGS
from vnpy.trader.database import get_database


def plot_tick_data(symbol: str, exchange: Exchange, start_date: str, end_date: str, time_range_seconds: int = 60):
    """
    获取指定合约每日 9:00 到 9:05 的 tick 数据并画图

    Parameters
    ----------
    symbol : str
        合约代码，例如 "IF2503"
    exchange : Exchange
        交易所，例如 Exchange.CFFEX
    start_date : str
        开始日期，格式 "YYYY-MM-DD"
    end_date : str
        结束日期，格式 "YYYY-MM-DD"
    time_range_seconds : int
        绘制的时间范围（秒），默认60秒
    """
    # 获取数据库
    database = get_database()
    print(f"数据库类型: {type(database).__name__}")
    print(f"合约: {symbol}.{exchange.value}")

    # 解析日期范围
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # 存储所有天的数据
    all_ticks = []
    dates = []

    # 遍历每一天
    current_date = start
    while current_date <= end:
        # 构造当天的 9:00 和 9:05 时间
        day_start = current_date.replace(hour=9, minute=0, second=0, microsecond=0)
        day_end = current_date.replace(hour=9, minute=5, second=0, microsecond=0)

        # 加载该时间段的数据
        ticks = database.load_tick_data(symbol, exchange, day_start, day_end)

        if ticks:
            print(f"✓ {current_date.strftime('%Y-%m-%d')}: 获取到 {len(ticks)} 条 tick 数据")
            # 转换为 DataFrame
            df = pd.DataFrame([
                {
                    'datetime': tick.datetime,
                    'last_price': tick.last_price,
                    'volume': tick.volume,
                    'bid_price_1': tick.bid_price_1,
                    'ask_price_1': tick.ask_price_1,
                    'bid_volume_1': tick.bid_volume_1,
                    'ask_volume_1': tick.ask_volume_1,
                }
                for tick in ticks
            ])
            df['date'] = current_date
            all_ticks.append(df)
            dates.append(current_date)
        else:
            print(f"✗ {current_date.strftime('%Y-%m-%d')}: 无数据")

        current_date += timedelta(days=1)

    if not all_ticks:
        print("未获取到任何数据，请检查合约代码和日期范围")
        return

    # 合并所有数据
    combined_df = pd.concat(all_ticks, ignore_index=True)
    print(f"\n总共获取 {len(combined_df)} 条 tick 数据，覆盖 {len(dates)} 个交易日")
    print(f"绘制时间范围: 前 {time_range_seconds} 秒")

    # 为每天分别画图
    for i, date in enumerate(dates):
        day_data = combined_df[combined_df['date'] == date].copy()

        if day_data.empty:
            continue

        print(f"\n正在绘制 {date.strftime('%Y-%m-%d')} 的图表...")

        # 将时间转换为当天的秒数
        day_data['time_seconds'] = day_data['datetime'].apply(
            lambda x: x.hour * 3600 + x.minute * 60 + x.second + x.microsecond / 1e6
        )

        # 只取指定时间范围的数据
        day_start_time = day_data['time_seconds'].min()
        day_data_filtered = day_data[day_data['time_seconds'] <= day_start_time + time_range_seconds].copy()

        # 创建图表
        fig, axes = plt.subplots(3, 1, figsize=(15, 12))
        fig.suptitle(f'{symbol}.{exchange.value} - {date.strftime("%Y-%m-%d")} 9:00-9:05 Tick 数据 (前{time_range_seconds}秒)',
                    fontsize=16, fontweight='bold')

        # 计算该天的刻度
        min_time = day_data_filtered['time_seconds'].min()
        max_time = day_data_filtered['time_seconds'].max()

        # 动态计算刻度间隔
        if time_range_seconds <= 30:
            tick_interval = 5  # 30秒以内，每5秒一个刻度
        elif time_range_seconds <= 120:
            tick_interval = 10  # 2分钟以内，每10秒一个刻度
        else:
            tick_interval = 30  # 更长时间，每30秒一个刻度

        time_ticks = list(range(int(min_time), int(max_time) + 1, tick_interval))
        # 只显示从9点开始的秒数
        time_labels = [f"{int(t - min_time)}" for t in time_ticks]

        # 图 1: 价格走势
        ax1 = axes[0]
        ax1.plot(day_data_filtered['time_seconds'], day_data_filtered['last_price'],
                color='blue', linewidth=2, label='最新价')
        ax1.set_xlabel('时间 (从9:00开始的秒数)', fontsize=12)
        ax1.set_ylabel('最新价', fontsize=12)
        ax1.set_title('价格走势', fontsize=14, fontweight='bold')
        ax1.grid(True, alpha=0.3)
        ax1.legend(loc='best', fontsize=10)
        ax1.set_xlim(min_time, max_time)
        ax1.set_xticks(time_ticks)
        ax1.set_xticklabels(time_labels)

        # 图 2: 买卖价差
        ax2 = axes[1]
        day_data_filtered['spread'] = day_data_filtered['ask_price_1'] - day_data_filtered['bid_price_1']
        ax2.plot(day_data_filtered['time_seconds'], day_data_filtered['spread'],
                color='orange', linewidth=2, label='买卖价差')
        ax2.set_xlabel('时间 (从9:00开始的秒数)', fontsize=12)
        ax2.set_ylabel('买卖价差', fontsize=12)
        ax2.set_title('买卖价差 (ask_price_1 - bid_price_1)', fontsize=14, fontweight='bold')
        ax2.grid(True, alpha=0.3)
        ax2.legend(loc='best', fontsize=10)
        ax2.set_xlim(min_time, max_time)
        ax2.set_xticks(time_ticks)
        ax2.set_xticklabels(time_labels)

        # 图 3: 成交量
        ax3 = axes[2]
        day_data_filtered['volume_delta'] = day_data_filtered['volume'].diff().fillna(0)
        ax3.bar(day_data_filtered['time_seconds'], day_data_filtered['volume_delta'],
               color='green', alpha=0.6, width=0.5, label='成交量')
        ax3.set_xlabel('时间 (从9:00开始的秒数)', fontsize=12)
        ax3.set_ylabel('成交量增量', fontsize=12)
        ax3.set_title('每笔成交量', fontsize=14, fontweight='bold')
        ax3.grid(True, alpha=0.3)
        ax3.legend(loc='best', fontsize=10)
        ax3.set_xlim(min_time, max_time)
        ax3.set_xticks(time_ticks)
        ax3.set_xticklabels(time_labels)

        plt.tight_layout()

        # 创建 pic 目录（如果不存在）
        pic_dir = Path(__file__).parent / 'pic'
        pic_dir.mkdir(exist_ok=True)

        # 保存该天的图片
        output_file = pic_dir / f"{symbol}_tick_{date.strftime('%Y-%m-%d')}_9to5.png"
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"✓ 图片已保存至: {output_file}")

        plt.close()  # 关闭图表，释放内存


def main():
    """主函数"""
    # 示例: 获取 IF2503.CFFEX 指数合约的 tick 数据
    # 请根据实际情况修改以下参数
    symbol = "sn2604"  # 合约代码
    exchange = Exchange.SHFE  # 交易所
    start_date = "2026-03-02"  # 开始日期
    end_date = "2026-03-06"  # 结束日期
    time_range_seconds = 120  # 绘制时间范围（秒）：30, 60, 120, 300 等

    print("=" * 60)
    print("DolphinDB Tick 数据获取与可视化")
    print("=" * 60)
    print(f"合约代码: {symbol}")
    print(f"交易所: {exchange.value}")
    print(f"日期范围: {start_date} 至 {end_date}")
    print(f"时间段: 每日 9:00 - 9:05")
    print(f"绘制范围: 前 {time_range_seconds} 秒")
    print("=" * 60)
    print()

    plot_tick_data(symbol, exchange, start_date, end_date, time_range_seconds)


if __name__ == "__main__":
    main()
