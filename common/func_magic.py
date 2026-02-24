import inspect
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


def run_limited_by_location(max_runs: int = 1):
    def decorator(func):
        # 外层字典：key = (filename, line_no) 或更详细的栈信息
        counters = {}

        def wrapper(*args, **kwargs):
            # 获取调用者的帧（跳过当前装饰器内部）
            frame = inspect.currentframe().f_back
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            key = (filename, lineno)  # 唯一标识一个静态调用位置

            if key not in counters:
                counters[key] = [0, []]
            count, results = counters[key]

            if count < max_runs:
                result = func(*args, **kwargs)
                counters[key][0] += 1
                counters[key][1].append(result)
                return result
            else:
                return results[-1] if results else None

        return wrapper
    return decorator

@run_limited_by_location(max_runs=1)
def print_msg_with_time_once(msg: str, print_func: callable = print) -> None:
    print_func(f'{msg} at {get_now_str()}')

@run_limited_by_location(max_runs=2)
def print_msg_with_time_twice(msg: str, print_func: callable = print) -> None:
    print_func(f'{msg} at {get_now_str()}')

@run_limited_by_location(max_runs=3)
def print_msg_with_time_third(msg: str, print_func: callable = print) -> None:
    print_func(f'{msg} at {get_now_str()}')

@run_limited_by_location(max_runs=5)
def print_msg_with_time_fifth(msg: str, print_func: callable = print) -> None:
    print_func(f'{msg} at {get_now_str()}')

@run_limited_by_location(max_runs=10)
def print_msg_with_time_tenth(msg: str, print_func: callable = print) -> None:
    print_func(f'{msg} at {get_now_str()}')

@run_limited_by_location(max_runs=20)
def print_msg_with_time_twentieth(msg: str, print_func: callable = print) -> None:
    print_func(f'{msg} at {get_now_str()}')

if __name__ == '__main__':

    for i in range(1,5):
        print_msg_with_time_twice("test01")  # 第一次执行（计数器独立）
        print_msg_with_time_twice("test02")  # 第一次执行（计数器独立）
        print_msg_with_time_twice("test03")  # 第一次执行（计数器独立）