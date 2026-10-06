"""모델 단건 응답의 ETag와 If-Match 조건 (ADR 0007, RFC 9110).

ETag는 `models.row_version`을 따옴표로 감싼 strong ETag다. 예: `"3"`
"""

import re
from dataclasses import dataclass

# If-Match = "*" / #entity-tag. entity-tag = [ "W/" ] DQUOTE *etagc DQUOTE
# 형식이 틀리면 헤더 검증에서 422로 알린다. 불일치(412)와 구분해 클라이언트가 원인을 바로 알게 한다.
_ENTITY_TAG = r'(?:W/)?"[^"]*"'
IF_MATCH_PATTERN = rf"^\s*(?:\*|{_ENTITY_TAG}(?:\s*,\s*{_ENTITY_TAG})*)\s*$"
_ENTITY_TAG_PARTS = re.compile(r'(W/)?"([^"]*)"')


def format_etag(row_version: int) -> str:
    return f'"{row_version}"'


@dataclass(frozen=True, slots=True)
class IfMatch:
    any: bool
    strong_tags: frozenset[str]

    def matches(self, row_version: int) -> bool:
        """If-Match는 strong 비교다. 약한 ETag(W/)는 어떤 값과도 일치하지 않는다."""
        return self.any or str(row_version) in self.strong_tags


def parse_if_match(value: str) -> IfMatch:
    """IF_MATCH_PATTERN으로 형식을 검증한 헤더 값을 해석한다."""
    if value.strip() == "*":
        return IfMatch(any=True, strong_tags=frozenset())
    tags = frozenset(
        match.group(2) for match in _ENTITY_TAG_PARTS.finditer(value) if match.group(1) is None
    )
    return IfMatch(any=False, strong_tags=tags)
