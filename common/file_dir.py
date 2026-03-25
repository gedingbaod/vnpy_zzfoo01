import os
from pathlib import Path
import json
import pandas as pd

from trader.utility import save_json, load_json

CURRENT_POSITION = "current_position.json"


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
        # df_sorted_desc = df.sort_values('delisted_date', ascending=True)
        return df.sort_values('delisted_date', ascending=True)
    except Exception as e:
        print(f"加载数据失败: {e}")
        return None

def save_dict_to_json(dict_data: dict, file_path: str) -> None:
    """
    将字典保存为JSON文件
    :param dict_data: 要保存的字典
    :param file_path: 保存路径（如 "data.json"）
    """
    # 确保目录存在
    dir_name = os.path.dirname(file_path)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name)

    # 保存字典到JSON文件
    with open(file_path, "w", encoding="utf-8") as f:
        # indent=4 格式化输出，便于阅读
        json.dump(dict_data, f, ensure_ascii=False, indent=4)
    print(f"字典已成功保存到 {file_path}")


def load_dict_from_json(file_path: str) -> dict:
    """
    从JSON文件读取字典
    :param file_path: 文件路径
    :return: 读取的字典
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件 {file_path} 不存在")

    with open(file_path, "r", encoding="utf-8") as f:
        dict_data = json.load(f)

    check_indicator(dict_data)

    print(f"已从 {file_path} 读取字典")
    return dict_data

def check_indicator(dict_data: dict) -> None:
    # 核心逻辑：遍历key，转换_indicator结尾的value为元组
    for key, value in dict_data.items():
        if key.endswith("_indicator"):
            # 确保值是可迭代类型，转换为元组（空值则转为空元组）
            if value is None:
                dict_data[key] = None
            elif isinstance(value, (list, tuple)):
                dict_data[key] = tuple(value)
            else:
                # 非可迭代类型（如单个值），转为单元素元组
                dict_data[key] = (value,)

# 主测试逻辑
if __name__ == "__main__":
    # 1. 定义测试字典
    test_dict = {
        "name": "张三",
        "age": 25,
        "hobbies": ["篮球", "编程", "阅读"],
        "scores": {"math": 90, "english": 85},
        "is_student": True,
        "open_indicator": (2.5, 3.6, 4.8),
        "close_indicator": (2.3, 3.5, 4.7),
        "non_indicator": None
    }

    # 2. 保存字典到JSON文件
    json_file = "test_dict.json"
    save_dict_to_json(test_dict, json_file)

    # 3. 从JSON文件读取字典
    loaded_dict = load_dict_from_json(json_file)

    # 4. 验证结果（对比原字典和读取的字典）
    print("\n   原字典：", test_dict)
    print("读取的字典：", loaded_dict)
    print("是否一致：", test_dict == loaded_dict)


    # 5. VNPY验证结果
    save_json(CURRENT_POSITION, test_dict)
    loaded_setting: dict = load_json(CURRENT_POSITION)
    check_indicator(loaded_setting)
    print("\n   原字典：", test_dict)
    print("读取的字典：", loaded_setting)
    print("是否一致：", test_dict == loaded_setting)