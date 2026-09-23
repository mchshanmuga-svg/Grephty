"""Small example of a rule used indirectly by an API endpoint."""


def resolve_scope(employee_count: int) -> str:
    return "small" if employee_count < 50 else "large"
