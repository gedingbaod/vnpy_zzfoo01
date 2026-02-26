"""
持仓历史数据保存模块
将套利策略的持仓历史数据保存到 MongoDB
"""

from datetime import datetime
from enum import Enum
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from common.vnpy_time import get_now_str
from vnpy.trader.setting import SETTINGS

# ================== 配置信息 ==================
USER_URI = "mongodb://vnpyUser:123456@192.168.31.28:27017/vnpy_position?authSource=admin"
TARGET_DB = "vnpy_position"
COLLECTION_NAME = "spread_position_history"
COLLECTION_NAME_TEST = "spread_position_history_test"

# 在模块顶部初始化一次
client = MongoClient(USER_URI)

def get_collection():
    db = client[TARGET_DB]
    mode = SETTINGS.get("spread.mode", "")
    if mode == "test":
        collection_name = COLLECTION_NAME_TEST
    else:
        collection_name = COLLECTION_NAME
    return db[collection_name]


def convert_enum_to_value(obj):
    """
    递归地将对象中的枚举类型转换为可序列化的值

    Parameters
    ----------
    obj : any
        任意对象

    Returns
    -------
    any
        转换后的对象
    """
    import enum
    from datetime import datetime, date, time

    if isinstance(obj, enum.Enum):
        # 枚举类型：返回 value
        return obj.value
    elif isinstance(obj, dict):
        # 字典：递归处理每个值
        return {k: convert_enum_to_value(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        # 列表或元组：递归处理每个元素
        return type(obj)(convert_enum_to_value(item) for item in obj)
    elif isinstance(obj, (datetime, date, time)):
        # 日期时间类型：转换为 ISO 格式字符串
        return obj.isoformat()
    else:
        # 其他类型：保持不变
        return obj


def save_position_to_mongodb(position: dict) -> bool:
    """
    保存单条持仓记录到 MongoDB（去重）

    利用唯一索引 (near_symbol + far_symbol + open_start_time) 自动去重
    如果记录已存在，则跳过；如果不存在，则插入

    Parameters
    ----------
    position : dict
        持仓记录字典，包含以下关键字段：
        - near_symbol: 近月合约
        - far_symbol: 远月合约
        - open_start_time: 开仓开始时间
        - 其他持仓字段...

    Returns
    -------
    bool
        True: 插入成功（新记录）
        False: 记录已存在或插入失败
    """
    try:
        collection = get_collection()

        # 构造唯一键过滤器
        near_symbol = position.get("near_symbol", "")
        far_symbol = position.get("far_symbol", "")
        open_start_time = position.get("open_start_time", "")

        # 如果缺少关键字段，无法保存
        if not near_symbol or not far_symbol:
            print(f"❌ 保存失败：缺少合约代码 (near_symbol={near_symbol}, far_symbol={far_symbol})")
            return False

        # 构造过滤器
        filter_dict = {
            "near_symbol": near_symbol,
            "far_symbol": far_symbol,
        }

        # 如果有 open_start_time，加入过滤条件
        if open_start_time:
            filter_dict["open_start_time"] = open_start_time

        # 添加保存时间戳
        position_copy = position.copy()
        position_copy["saved_at"] = datetime.now().isoformat()

        # 转换枚举类型为可序列化的值
        position_copy = convert_enum_to_value(position_copy)

        # 使用 update_one with upsert=True 实现去重插入
        # $setOnInsert: 只在插入新文档时设置字段，已存在时不更新
        result = collection.update_one(
            filter_dict,
            {"$setOnInsert": position_copy},
            upsert=True
        )

        # 判断是插入还是跳过
        if result.upserted_id is not None:
            # print(f"✅ 持仓记录已保存: {near_symbol}/{far_symbol} {open_start_time}")
            return True
        else:
            # print(f"ℹ️ 持仓记录已存在，跳过: {near_symbol}/{far_symbol} {open_start_time}")
            return False

    except DuplicateKeyError:
        # 理论上不会走到这里，因为已经用了 upsert
        print(f"ℹ️ 持仓记录已存在（重复键）: {position.get('near_symbol')}/{position.get('far_symbol')} at {get_now_str()}")
        return False
    except Exception as e:
        print(f"❌ 保存持仓记录失败: {str(e)} at {get_now_str()}")
        import traceback
        traceback.print_exc()
        return False


def save_positions_to_mongodb(positions: list) -> dict:
    """
    批量保存持仓记录到 MongoDB（去重）

    Parameters
    ----------
    positions : list
        持仓记录列表

    Returns
    -------
    dict
        统计信息：
        {
            "inserted": 插入数量,
            "skipped": 跳过数量,
            "total": 总数量
        }
    """
    inserted_count = 0
    skipped_count = 0

    for position in positions:
        if save_position_to_mongodb(position):
            inserted_count += 1
        else:
            skipped_count += 1

    result = {
        "inserted": inserted_count,
        "skipped": skipped_count,
        "total": len(positions)
    }

    # print(f"\n📊 批量保存完成: 插入 {result['inserted']} 条，跳过 {result['skipped']} 条，总计 {result['total']} 条")

    return result


def position_exists_in_mongodb(position: dict) -> bool:
    """
    检查持仓记录是否已存在于 MongoDB

    Parameters
    ----------
    position : dict
        持仓记录字典

    Returns
    -------
    bool
        True: 记录已存在
        False: 记录不存在
    """
    try:
        collection = get_collection()

        near_symbol = position.get("near_symbol", "")
        far_symbol = position.get("far_symbol", "")
        open_start_time = position.get("open_start_time", "")

        filter_dict = {
            "near_symbol": near_symbol,
            "far_symbol": far_symbol,
        }

        if open_start_time:
            filter_dict["open_start_time"] = open_start_time

        count = collection.count_documents(filter_dict)
        return count > 0

    except Exception as e:
        print(f"❌ 检查持仓记录是否存在失败: {str(e)}")
        return False


# ================== 测试代码 ==================
if __name__ == "__main__":
    print("=" * 50)
    print("持仓历史数据保存测试")
    print("=" * 50)

    # 测试数据
    test_position = {
        "near_symbol": "sn2603",
        "far_symbol": "sn2604",
        "open_start_time": "2026-02-26 10:00:00",
        "open_finish_time": "2026-02-26 10:01:00",
        "close_start_time": "2026-02-26 14:00:00",
        "close_finish_time": "2026-02-26 14:01:00",
        "position_status": "CLOSED",
        "near_open_order_id": "TEST_NEAR_OPEN",
        "far_open_order_id": "TEST_FAR_OPEN",
        "near_close_order_id": "TEST_NEAR_CLOSE",
        "far_close_order_id": "TEST_FAR_CLOSE",
        "near_volume": 1,
        "far_volume": 1,
        "test": True
    }

    # 测试插入
    print("\n1. 测试插入新记录...")
    result1 = save_position_to_mongodb(test_position)
    print(f"结果: {'成功' if result1 else '失败'}")

    # 测试重复插入
    print("\n2. 测试重复插入（应该跳过）...")
    result2 = save_position_to_mongodb(test_position)
    print(f"结果: {'成功' if result2 else '失败（已跳过）'}")

    # 测试检查是否存在
    print("\n3. 测试检查记录是否存在...")
    exists = position_exists_in_mongodb(test_position)
    print(f"记录存在: {exists}")

    # 测试批量插入
    print("\n4. 测试批量插入...")
    test_positions = [
        test_position,
        {
            "near_symbol": "ni2603",
            "far_symbol": "ni2605",
            "open_start_time": "2026-02-26 11:00:00",
            "position_status": "OPEN",
            "test": True
        },
        {
            "near_symbol": "ni2603",
            "far_symbol": "ni2605",
            "open_start_time": "2026-02-26 12:00:00",
            "position_status": "OPEN",
            "test": True
        }
    ]
    batch_result = save_positions_to_mongodb(test_positions)
    print(f"批量插入结果: {batch_result}")

    print("\n✅ 测试完成！")
