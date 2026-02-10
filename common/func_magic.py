from common.vnpy_time import get_now_str


def run_once(func):
    """装饰器：让函数仅执行一次"""
    # 用闭包保存执行状态
    is_called = False
    result = None  # 保存函数执行结果（可选）

    def wrapper(*args, **kwargs):
        nonlocal is_called, result
        if not is_called:
            result = func(*args, **kwargs)
            is_called = True
        return result  # 后续调用返回第一次执行的结果
    return wrapper

@run_once
def print_msg_with_time_once(msg: str):
    print(f'{msg} at {get_now_str()}')


def run_limited(max_runs: int = 1):
    """
    装饰器：限制函数的最大执行次数
    :param max_runs: 最大执行次数（默认1次，这里设为2即可）
    """

    def decorator(func):
        # 闭包中维护执行次数和结果（复用第一次/第二次的执行结果）
        run_count = 0
        results = []  # 保存每次执行的结果

        def wrapper(*args, **kwargs):
            nonlocal run_count
            # 仅当执行次数未达上限时，执行函数
            if run_count < max_runs:
                result = func(*args, **kwargs)
                run_count += 1
                results.append(result)
                return result
            # 执行次数达上限后，返回最后一次执行的结果（可选）
            return results[-1] if results else None

        return wrapper

    return decorator

@run_limited(max_runs=2)
def print_msg_with_time_twice(msg: str):
    print(f'{msg} at {get_now_str()}')

@run_limited(max_runs=3)
def print_msg_with_time_third(msg: str):
    print(f'{msg} at {get_now_str()}')

@run_limited(max_runs=5)
def print_msg_with_time_fifth(msg: str):
    print(f'{msg} at {get_now_str()}')

@run_limited(max_runs=10)
def print_msg_with_time_tenth(msg: str):
    print(f'{msg} at {get_now_str()}')

@run_limited(max_runs=20)
def print_msg_with_time_twentieth(msg: str):
    print(f'{msg} at {get_now_str()}')