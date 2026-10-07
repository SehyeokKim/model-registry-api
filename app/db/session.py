"""DB 엔진과 세션 팩토리. 엔진은 앱 수명 동안 하나를 공유한다."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

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
        # DB 오류 메시지에 SQL 파라미터(고객이 보낸 이름, 설명, 스키마)를 넣지 않는다.
        # 예외가 로그로 남을 때 요청 본문이 함께 남는 것을 막는다.
        hide_parameters=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """트랜잭션을 명시적으로 시작하는 세션을 만든다.

    - autobegin=False: `with session.begin():` 없이 쿼리하면 오류가 나므로,
      트랜잭션 경계가 서비스 계층 코드에 항상 드러난다.
    - expire_on_commit=False: 커밋 후에도 응답 직렬화에 객체를 그대로 쓴다.
    """
    return sessionmaker(bind=engine, autobegin=False, expire_on_commit=False)
