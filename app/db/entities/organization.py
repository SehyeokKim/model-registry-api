from sqlalchemy import String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class Organization(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """고객사(테넌트). 모델과 데이터셋의 소유 단위다 (ADR 0006)."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(100))
    # 고객 데이터를 내부 품질 분석에 써도 된다는 계약상 동의. 분석 레이크의 조직 행 필터가 사용한다.
    analytics_consent: Mapped[bool] = mapped_column(server_default=false())
