"""DB 엔진 생성. 엔진은 앱 수명 동안 하나를 공유한다."""

from sqlalchemy import Engine, create_engine

from app.core.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    """연결 예산(ADR 0002)에 맞춘 커넥션 풀로 엔진을 만든다. 생성 시점에는 접속하지 않는다."""
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        # 끊어진 연결(DB 재시작, 방화벽 유휴 종료)을 꺼내 쓰기 전에 걸러낸다.
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )
