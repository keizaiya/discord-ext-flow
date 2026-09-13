#!/usr/bin/env python3
# ruff: noqa: D101,D103,T201
from __future__ import annotations

import argparse
import io
import sys
import token
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


RULE = 'MLP001'
OPEN_TO_CLOSE = {'(': ')', '[': ']', '{': '}'}
CLOSE_TO_OPEN = {close: open_ for open_, close in OPEN_TO_CLOSE.items()}
IGNORED = {token.ENCODING, token.INDENT, token.DEDENT, token.NEWLINE, token.NL, token.COMMENT}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    path: str
    line: int
    column: int

    def __str__(self) -> str:
        return (
            f'{self.path}:{self.line}:{self.column}: '
            f'{RULE} multiple parameters share a line '
            'in a multiline parameter list'
        )


def parameter_open(tokens: Sequence[tokenize.TokenInfo], def_index: int) -> int | None:
    """Find the '(' that opens a function's parameter list."""
    stack: list[str] = []
    for index in range(def_index + 1, len(tokens)):
        current = tokens[index]
        if current.type != token.OP:
            continue
        value = current.string
        if value == '(' and not stack:
            return index
        if value in OPEN_TO_CLOSE:
            stack.append(value)
        elif value in CLOSE_TO_OPEN and stack and stack[-1] == CLOSE_TO_OPEN[value]:
            stack.pop()
    return None


def matching_close(tokens: Sequence[tokenize.TokenInfo], open_index: int) -> int | None:
    """Find the ')' matching the parameter-list '('."""
    stack = [tokens[open_index].string]
    for index in range(open_index + 1, len(tokens)):
        current = tokens[index]
        if current.type != token.OP:
            continue
        value = current.string
        if value in OPEN_TO_CLOSE:
            stack.append(value)
            continue
        if value not in CLOSE_TO_OPEN or not stack:
            continue
        if stack[-1] != CLOSE_TO_OPEN[value]:
            return None
        stack.pop()
        if not stack:
            return index

    return None


def top_level_commas(tokens: Sequence[tokenize.TokenInfo], open_index: int, close_index: int) -> Iterator[int]:
    """Yield commas separating function parameters.

    Commas inside nested (), [], {} are ignored.

    Also handles unparenthesized lambdas such as:

        def foo(
            callback=lambda a, b: (a, b),
            other=1,
        ): ...
    """
    stack = ['(']
    lambda_headers = 0

    for index in range(open_index + 1, close_index):
        current = tokens[index]
        if len(stack) == 1 and current.type == token.NAME and current.string == 'lambda':
            lambda_headers += 1
            continue
        if current.type != token.OP:
            continue
        value = current.string
        if value in OPEN_TO_CLOSE:
            stack.append(value)
            continue
        if value in CLOSE_TO_OPEN:
            if stack and stack[-1] == CLOSE_TO_OPEN[value]:
                stack.pop()
            continue
        if len(stack) != 1:
            continue
        if value == ':' and lambda_headers:
            lambda_headers -= 1
            continue
        if value == ',' and not lambda_headers:
            yield index


def significant_tokens(tokens: Sequence[tokenize.TokenInfo], start: int, stop: int) -> list[tokenize.TokenInfo]:
    return [current for current in tokens[start:stop] if current.type not in IGNORED]


def is_parameter_segment(items: Sequence[tokenize.TokenInfo]) -> bool:
    if not items:
        return False
    # `/` and bare `*` are parameter-list separators,
    # not parameters themselves.
    return not (len(items) == 1 and items[0].type == token.OP and items[0].string in {'/', '*'})


def violations_in_parameter_list(
    tokens: Sequence[tokenize.TokenInfo],
    open_index: int,
    close_index: int,
) -> Iterator[tokenize.TokenInfo]:
    opening = tokens[open_index]
    closing = tokens[close_index]
    # Entire parameter list is one line: allowed.
    if opening.start[0] == closing.start[0]:
        return
    commas = list(top_level_commas(tokens, open_index, close_index))
    boundaries = [*commas, close_index]
    segment_start = open_index + 1
    previous_parameter_comma_line: int | None = None
    for boundary in boundaries:
        items = significant_tokens(tokens, segment_start, boundary)
        if is_parameter_segment(items):
            first = items[0]
            if previous_parameter_comma_line == first.start[0]:
                yield first
            previous_parameter_comma_line = tokens[boundary].start[0] if boundary != close_index else None
        segment_start = boundary + 1


def lint_bytes(source: bytes, path: str) -> list[Diagnostic]:
    tokens = list(tokenize.tokenize(io.BytesIO(source).readline))
    diagnostics: list[Diagnostic] = []
    index = 0

    while index < len(tokens):
        current = tokens[index]
        if current.type != token.NAME or current.string != 'def':
            index += 1
            continue
        open_index = parameter_open(tokens, index)
        if open_index is None:
            index += 1
            continue
        close_index = matching_close(tokens, open_index)
        if close_index is None:
            index += 1
            continue
        diagnostics.extend(
            Diagnostic(path=path, line=violation.start[0], column=violation.start[1] + 1)
            for violation in violations_in_parameter_list(tokens, open_index, close_index)
        )
        index = close_index + 1

    return diagnostics


def lint_file(path: Path) -> list[Diagnostic]:
    return lint_bytes(path.read_bytes(), str(path))


def python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(candidate for candidate in path.rglob('*.py') if candidate.is_file())
        elif path.is_file():
            yield path
        else:
            raise FileNotFoundError(path)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Reject multiple parameters on one line when a function parameter list spans multiple lines.'
            f'like: `{sys.argv[0]} packages main.py foo_lib`'
        ),
    )
    parser.add_argument('paths', nargs='+', type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    had_error = False
    had_violation = False

    try:
        paths = list(python_files(args.paths))
    except OSError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    for path in paths:
        try:
            diagnostics = lint_file(path)
        except (OSError, SyntaxError, tokenize.TokenError) as exc:
            print(f'{path}: error: {exc}', file=sys.stderr)
            had_error = True
            continue
        for diagnostic in diagnostics:
            print(diagnostic)
        had_violation |= bool(diagnostics)

    if had_error:
        return 2
    if had_violation:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
