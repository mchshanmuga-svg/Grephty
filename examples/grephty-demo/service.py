from scope import resolve_scope


def approve(employee_count: int) -> dict:
    return {"scope": resolve_scope(employee_count), "approved": True}
