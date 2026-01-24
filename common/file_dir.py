import os
from pathlib import Path

import pandas as pd


def traverse_file_in_dir(csv_dir):
    print("==== 成交量分析程序 ====")

    # 指定CSV文件夹路径
    # csv_dir = r"D:\02.QUANT\Quant\volume-analysis\csv"

    base_path = Path(csv_dir)

    # 检查CSV文件夹是否存在
    if not os.path.exists(csv_dir):
        print(f"错误: CSV文件夹 '{csv_dir}' 不存在")
        return None

    # 获取CSV文件夹中的所有CSV文件
    csv_files = [base_path.joinpath(csv_dir, f) for f in os.listdir(csv_dir) if f.endswith('.csv')]

    if not csv_files:
        print(f"CSV文件夹 '{csv_dir}' 中没有找到CSV文件")
        return None

    print(f"找到{len(csv_files)}个CSV文件，开始处理...")

    # # 逐个处理每个CSV文件
    # for csv_file in csv_files:
    #     process_single_file(csv_file, csv_dir)

    print("\n所有文件处理完成！")

    return csv_files


def load_data_from_csv(file_path):
    """加载CSV数据"""
    try:
        df = pd.read_csv(file_path)
        # # 将字符串日期列转换为datetime格式，这里不能带后缀unit='s'
        df['listed_date'] = pd.to_datetime(df['listed_date'])
        df['delisted_date'] = pd.to_datetime(df['delisted_date'])
        # # 从Timestamp转换为datetime类型
        # df['listed_date'] = df['listed_date'].dt.to_pydatetime()
        # df['delisted_date'] = df['delisted_date'].dt.to_pydatetime()

        # df['listed_date'] = pd.to_datetime(df['listed_date']).dt.to_pydatetime()
        # df['delisted_date'] = pd.to_datetime(df['delisted_date']).dt.to_pydatetime()
        df = df.sort("delisted_date")
        return df
    except Exception as e:
        print(f"加载数据失败: {e}")
        return None