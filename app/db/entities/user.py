import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, string_enum
from app.db.entities.enums import UserRole


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """고객 또는 내부 사용자. 고객만 조직에 속한다 (ADR 0006)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(role = 'CUSTOMER') = (organization_id IS NOT NULL)",
            name="customer_has_organization",
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[UserRole] = mapped_column(string_enum(UserRole, "role"))
