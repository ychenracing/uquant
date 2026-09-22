"""Locate negative-test stimuli by Python tokens, not formatter whitespace."""
from __future__ import annotations

import io
import tokenize


def fragment_spans(source: str, fragment: str) -> list[tuple[int, int]]:
    ignored = {tokenize.ENCODING, tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
               tokenize.DEDENT, tokenize.COMMENT, tokenize.ENDMARKER}
    def tokens(text: str) -> list[tokenize.TokenInfo]:
        result = []
        iterator = tokenize.generate_tokens(io.StringIO(text).readline)
        try:
            for token in iterator:
                if token.type not in ignored:
                    result.append(token)
        except tokenize.TokenError:
            # A stimulus is often a deliberately incomplete call fragment.
            # The complete source is independently parsed by the caller.
            if text == source:
                raise
        return result
    actual, wanted = tokens(source), tokens(fragment)
    assert wanted, "empty mutation stimulus"
    expected = [(token.type, token.string) for token in wanted]
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    def offset(position: tuple[int, int]) -> int:
        return starts[position[0] - 1] + position[1]
    return [(offset(actual[index].start), offset(actual[index + len(wanted) - 1].end))
            for index in range(len(actual) - len(wanted) + 1)
            if [(token.type, token.string) for token in actual[index:index + len(wanted)]] == expected]
