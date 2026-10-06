import re

import pytest

from app.core.etag import IF_MATCH_PATTERN, format_etag, parse_if_match


def test_etag_is_quoted_row_version() -> None:
    assert format_etag(3) == '"3"'


class TestIfMatchPattern:
    @pytest.mark.parametrize(
        "value", ['"3"', "*", '"2", "3"', '"2","3"', 'W/"3"', ' "3" ', 'W/"1", "3"', '""']
    )
    def test_valid_values(self, value: str) -> None:
        assert re.match(IF_MATCH_PATTERN, value)

    @pytest.mark.parametrize("value", ["3", '"3', '3"', "W/3", '"3" "4"', '*, "3"', "", " "])
    def test_malformed_values(self, value: str) -> None:
        assert not re.match(IF_MATCH_PATTERN, value)


class TestIfMatch:
    def test_matching_strong_tag(self) -> None:
        assert parse_if_match('"3"').matches(3)

    def test_different_tag_does_not_match(self) -> None:
        assert not parse_if_match('"2"').matches(3)

    def test_any_tag_in_list_matches(self) -> None:
        assert parse_if_match('"1", "3"').matches(3)

    def test_wildcard_matches_any_version(self) -> None:
        assert parse_if_match("*").matches(42)

    def test_weak_tag_never_matches(self) -> None:
        # RFC 9110: If-Match는 strong 비교를 쓴다.
        assert not parse_if_match('W/"3"').matches(3)
        assert parse_if_match('W/"3", "3"').matches(3)
