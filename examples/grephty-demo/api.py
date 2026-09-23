from service import approve


def endpoint(employee_count: int) -> dict:
    return approve(employee_count)
