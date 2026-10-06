## 개요
<!-- 이 PR이 무엇을, 왜 바꾸는지 한두 문장으로 -->

## 변경 내용
-

## 테스트 방법
<!-- 리뷰어가 그대로 따라 할 수 있게 -->
```bash
docker compose up -d --build
```

## 체크리스트
- [ ] `uv run ruff check .` / `uv run ruff format --check .` 통과
- [ ] `uv run pytest` 통과
- [ ] `docker compose up -d`로 기동 및 마이그레이션 확인
- [ ] API 변경 시 OpenAPI 문서 반영
- [ ] 스키마 변경 시 마이그레이션 포함, `docs/erd.md` 갱신
- [ ] 관련 ADR: <!-- 없으면 "없음" -->

## 참고
<!-- 리뷰 포인트, 남은 작업, 트레이드오프 -->
