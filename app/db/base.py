"""ORM 기반 클래스와 공통 컬럼."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, MetaData, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 제약과 인덱스 이름을 고정한다. 마이그레이션 결과가 일정해지고,
# 서비스 계층에서 IntegrityError를 제약 이름으로 구분할 수 있다.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        uuid.UUID: Uuid(),
        dict[str, Any]: JSONB(),
    }


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()"), sort_order=-10
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


def string_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """PostgreSQL ENUM 타입 대신 varchar + CHECK 제약으로 저장한다.

    ENUM 타입은 값을 추가하거나 바꾸는 마이그레이션이 번거롭다. CHECK 제약 이름은
    naming convention에 따라 `ck_<table>_<name>`이 된다.
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )
