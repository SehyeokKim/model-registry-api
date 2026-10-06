from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class Organization(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """고객사(테넌트). 모델과 데이터셋의 소유 단위다 (ADR 0006)."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(100))
