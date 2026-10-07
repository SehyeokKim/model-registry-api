"""README의 에러 코드 표가 카탈로그(ERROR_CATALOG)와 같은지 확인한다 (ADR 0008).

클라이언트 개발자는 README 표를 보고 분기를 만든다. 코드를 추가하거나 조건을 바꾸고 표를 고치지
않으면 이 테스트가 실패한다.
"""

import re
from pathlib import Path

from app.core.errors import ERROR_CATALOG

README = Path(__file__).resolve().parents[2] / "README.md"
_ROW = re.compile(r"^\| `([A-Z_]+)` \| (\d{3}) \| (.+) \|$")


def _error_table() -> dict[str, tuple[int, str]]:
    section = README.read_text(encoding="utf-8").split("## 에러 코드", 1)[1].split("\n## ", 1)[0]
    rows = (_ROW.match(line) for line in section.splitlines())
    return {m.group(1): (int(m.group(2)), m.group(3)) for m in rows if m}


def test_readme_error_table_matches_catalog() -> None:
    expected = {code.value: (spec.status, spec.condition) for code, spec in ERROR_CATALOG.items()}

    assert _error_table() == expected
