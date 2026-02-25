import sys
from math import floor, ceil

# 其他常量
MAX_FLOAT = sys.float_info.max

def adjust_price(price: float) -> float:
    """将异常的浮点数最大值（MAX_FLOAT）数据调整为0"""
    if price == MAX_FLOAT:
        price = 0
    return price

def round_to_10(x: float, mode="round") -> float:
    """
    将数值规整为10的整数倍
    :param x: 原始数值
    :param mode: 取整规则：round(四舍五入)、floor(向下取整)、ceil(向上取整)
    :return: 10的整数倍数值
    """
    if mode == "round":
        return float(round(x / 10) * 10)  # 四舍五入（如14→10，16→20）
    elif mode == "floor":
        return float((x // 10) * 10)      # 向下取整（如19→10，21→20）
    elif mode == "ceil":
        return float(((x + 9) // 10) * 10) # 向上取整（如11→20，20→20）
    else:
        raise ValueError("mode只能是round/floor/ceil")

def round_to_1(x: float, mode="round") -> float:
    """
    将数值规整为10的整数倍
    :param x: 原始数值
    :param mode: 取整规则：round(四舍五入)、floor(向下取整)、ceil(向上取整)
    :return: 10的整数倍数值
    """
    if mode == "round":
        return float(round(x))  # 四舍五入（如14→10，16→20）
    elif mode == "floor":
        return float(floor(x))      # 向下取整（如19→10，21→20）
    elif mode == "ceil":
        return float(ceil(x)) # 向上取整（如11→20，20→20）
    else:
        raise ValueError("mode只能是round/floor/ceil")

def round_to_02(x: float, mode="round") -> float:
    """
    将数值规整为0.2的倍数
    :param x: 原始数值（浮点数）
    :param mode: 取整规则：round(四舍五入)、floor(向下取整)、ceil(向上取整)
    :return: 0.2的倍数数值（浮点数，精准保留1位小数）
    """
    # 先将数值转换为0.2倍数的基数（除以0.2）
    base = x / 0.2

    # 根据mode选择取整方式
    if mode == "round":
        result_base = round(base)  # 四舍五入取整
    elif mode == "floor":
        result_base = floor(base)  # 向下取整
    elif mode == "ceil":
        result_base = ceil(base)  # 向上取整
    else:
        raise ValueError("mode只能是round/floor/ceil")

    # 还原为0.2的倍数，并保留1位小数解决浮点精度问题
    final_result = round(result_base * 0.2, 1)
    return final_result

if __name__ == "__main__":

    ret = round_to_10(19856, mode="round")
    print(ret)

    ret = round_to_1(41.2, "ceil")
    print(type(ret))
    print(ret)
    print(round_to_1(41.5, "floor"))

    # 测试示例
    test_nums = [1.1, 1.2, 1.3, 2.45, 3.7, -0.1, -0.3]
    for num in test_nums:
        result = round_to_02(num)
        print(f"{num} → 0.2单位结果：{result}")