from sqlalchemy.exc import IntegrityError


def violated_constraint(error: IntegrityError) -> str | None:
    """IntegrityError에서 위반된 제약 이름을 꺼낸다.

    제약 이름은 naming convention으로 고정되어 있어 코드에서 비교할 수 있다.
    """
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
