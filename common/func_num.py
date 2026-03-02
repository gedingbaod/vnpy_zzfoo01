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


def round_to_price_unit(x: float, unit: float, mode: str = "round") -> float:
    """
    将数值规整为指定价格单位的倍数
    :param x: 原始小数价格（如1.35）
    :param unit: 价格单位（如1, 2, 5, 10, 0.1, 0.2, 0.5, 0.02, 0.05）
    :param mode: 取整规则：round(四舍五入)、floor(向下取整)、ceil(向上取整)
    :return: 规整后的价格（浮点数，保留与单位匹配的小数位数）
    """
    # 校验输入合法性
    if unit <= 0:
        raise ValueError("价格单位unit必须是正数（如0.02、0.5、1等）")
    valid_modes = ["round", "floor", "ceil"]
    if mode not in valid_modes:
        raise ValueError(f"mode只能是{valid_modes}中的一种")

    # 核心逻辑：先转换为单位基数，再取整，最后还原
    base = x / unit  # 转换为单位基数（如1.35 / 0.05 = 27）

    if mode == "round":
        result_base = round(base)
    elif mode == "floor":
        result_base = floor(base)
    else:  # ceil
        result_base = ceil(base)

    # 还原为单位倍数，并处理浮点精度问题
    final_result = result_base * unit

    # 自动匹配单位的小数位数（避免0.2→1.2000000000000002这类问题）
    # 提取单位的小数位数：如0.05→2位，0.2→1位，5→0位
    unit_str = str(unit)
    if "." in unit_str:
        decimal_digits = len(unit_str.split(".")[-1])
        final_result = round(final_result, decimal_digits)

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

        # 测试用例：覆盖所有指定单位和不同取整模式
    test_cases = [
        (1.35, 0.05, "round"),  # 1.35 → 0.05单位 → 1.35（刚好倍数）
        (1.37, 0.05, "round"),  # 1.37 → 0.05单位 → 1.35（四舍五入）
        (1.1, 0.2, "round"),  # 1.1 → 0.2单位 → 1.0
        (1.3, 0.2, "round"),  # 1.3 → 0.2单位 → 1.2
        (2.8, 1, "ceil"),  # 2.8 → 1单位 → 3
        (5.2, 2, "floor"),  # 5.2 → 2单位 → 4
        (0.37, 0.02, "round"),  # 0.37 → 0.02单位 → 0.38
        (7.8, 5, "round"),  # 7.8 → 5单位 → 10
        (154972, 5, "round")
    ]

    for x, unit, mode in test_cases:
        try:
            res = round_to_price_unit(x, unit, mode)
            print(f"原始值：{x} | 单位：{unit} | 模式：{mode} → 结果：{res}")
        except ValueError as e:
            print(f"错误：{e}")