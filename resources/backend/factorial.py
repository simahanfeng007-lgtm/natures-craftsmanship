def factorial(n):
    """计算非负整数 n 的阶乘。
    
    Args:
        n: 非负整数
    
    Returns:
        n 的阶乘值
    
    Raises:
        ValueError: 如果 n 为负数或不是整数
    """
    if not isinstance(n, int) or n < 0:
        raise ValueError("输入必须是非负整数")
    if n == 0:
        return 1
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result


if __name__ == "__main__":
    # 简单测试
    print(f"5! = {factorial(5)}")
    print(f"0! = {factorial(0)}")
    print(f"10! = {factorial(10)}")
