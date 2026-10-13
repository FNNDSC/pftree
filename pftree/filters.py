"""Token filters for file names and directory paths.

A filter specification is a comma-separated list of tokens. Each token is one of:

    nii              substring: the text contains ``nii``
    glob:*.nii.gz    shell glob (fnmatch) over the whole text
    re:^sub-\\d+$     regular expression, searched anywhere in the text
    !tmp             negation of any of the above forms

Tokens combine under a logic, ``OR`` (any token matches) or ``AND`` (every token matches).
File filters test a file's name; directory filters test a directory's path relative to
the input root, with ``/`` separators, so the input root's own name never matches and
``glob:*/anat`` or ``re:/anat$`` can anchor on components.

Key components:
    - FilterToken: one parsed token with a ``matches`` method
    - FilterLogic: the AND / OR combinator
    - tokens_parse: specification string to list of tokens
    - text_matches: one text against a token list under a logic
    - fileNames_filter: keep the file names a filter accepts
    - dirPath_matches: does a relative directory path pass a filter

Typical usage:
    tokens = tokens_parse("glob:*.nii.gz,!tmp")
    kept = fileNames_filter(["a.nii.gz", "tmp.nii.gz"], tokens, FilterLogic.AND)

Dependencies:
    - standard library only
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

GLOB_PREFIX: str = "glob:"
REGEX_PREFIX: str = "re:"
NEGATE_PREFIX: str = "!"
TOKEN_SEPARATOR: str = ","

TokenKind = Literal["substring", "glob", "regex"]


class FilterLogic(Enum):
    """How the tokens of one filter combine.

    Attributes:
        OR: a text passes when any token matches.
        AND: a text passes only when every token matches.

    Example:
        >>> FilterLogic.parse("and")
        <FilterLogic.AND: 'AND'>
    """

    OR = "OR"
    AND = "AND"

    @classmethod
    def parse(cls, text: str) -> FilterLogic:
        """Read a logic name case-insensitively.

        Args:
            text: ``"AND"`` or ``"OR"`` in any case.

        Returns:
            The matching FilterLogic member.

        Raises:
            ValueError: if the text is neither AND nor OR.
        """
        normalized: str = text.strip().upper()
        try:
            return cls(normalized)
        except ValueError as err:
            raise ValueError(f"filter logic must be AND or OR, not {text!r}") from err


@dataclass(frozen=True)
class FilterToken:
    """One parsed filter token.

    Attributes:
        kind: ``"substring"``, ``"glob"`` or ``"regex"``.
        pattern: the text after any prefixes.
        negate: True when the token was written with a leading ``!``.

    Example:
        >>> FilterToken.parse("!glob:*.bak").matches("notes.bak")
        False
    """

    kind: TokenKind
    pattern: str
    negate: bool = False

    @classmethod
    def parse(cls, text: str) -> FilterToken:
        """Build a token from its written form.

        Args:
            text: one token, already split from the comma list, whitespace allowed.

        Returns:
            The parsed token. An unknown ``word:`` prefix is not special; the whole
            text is a substring pattern.

        Raises:
            ValueError: if the token is empty, or a regex pattern does not compile.
        """
        body: str = text.strip()
        negate: bool = body.startswith(NEGATE_PREFIX)
        if negate:
            body = body[len(NEGATE_PREFIX) :]
        if not body:
            raise ValueError(f"empty filter token in {text!r}")
        if body.startswith(GLOB_PREFIX):
            return cls("glob", body[len(GLOB_PREFIX) :], negate)
        if body.startswith(REGEX_PREFIX):
            pattern: str = body[len(REGEX_PREFIX) :]
            try:
                re.compile(pattern)
            except re.error as err:
                raise ValueError(f"bad regular expression {pattern!r}: {err}") from err
            return cls("regex", pattern, negate)
        return cls("substring", body, negate)

    def matches(self, text: str) -> bool:
        """Test a text against this token, honouring negation.

        Args:
            text: a file name, or a directory path relative to the input root.

        Returns:
            True when the token accepts the text.
        """
        hit: bool
        match self.kind:
            case "glob":
                hit = fnmatch.fnmatchcase(text, self.pattern)
            case "regex":
                hit = re.search(self.pattern, text) is not None
            case _:
                hit = self.pattern in text
        return hit != self.negate


def tokens_parse(spec: str) -> list[FilterToken]:
    """Split a comma-separated specification into tokens.

    Args:
        spec: the ``--fileFilter`` or ``--dirFilter`` value. Empty means no filter.

    Returns:
        The tokens in written order; an empty list for an empty specification.

    Raises:
        ValueError: if any token is empty or malformed.

    Example:
        >>> [t.kind for t in tokens_parse("nii,glob:*.gz")]
        ['substring', 'glob']
    """
    if not spec.strip():
        return []
    return [FilterToken.parse(part) for part in spec.split(TOKEN_SEPARATOR)]


def text_matches(text: str, tokens: list[FilterToken], logic: FilterLogic) -> bool:
    """Combine every token's verdict on one text.

    Args:
        text: the text under test.
        tokens: parsed tokens; an empty list accepts everything.
        logic: AND requires every token, OR any token.

    Returns:
        True when the text passes the filter.
    """
    if not tokens:
        return True
    verdicts: list[bool] = [token.matches(text) for token in tokens]
    if logic is FilterLogic.AND:
        return all(verdicts)
    return any(verdicts)


def fileNames_filter(names: list[str], tokens: list[FilterToken], logic: FilterLogic) -> list[str]:
    """Keep the file names a filter accepts, sorted.

    Args:
        names: bare file names from one directory.
        tokens: parsed ``--fileFilter`` tokens.
        logic: the ``--fileFilterLogic`` combinator.

    Returns:
        The accepted names in sorted order.

    Example:
        >>> fileNames_filter(["b.nii", "a.nii", "c.txt"], tokens_parse("nii"), FilterLogic.OR)
        ['a.nii', 'b.nii']
    """
    return sorted(name for name in names if text_matches(name, tokens, logic))


def dirPath_matches(relPath: str, tokens: list[FilterToken], logic: FilterLogic) -> bool:
    """Decide whether a directory passes the directory filter.

    Args:
        relPath: the directory's path relative to the input root, ``/`` separated,
            ``"."`` for the root itself.
        tokens: parsed ``--dirFilter`` tokens.
        logic: the ``--dirFilterLogic`` combinator.

    Returns:
        True when the directory is kept. The root (``"."``) passes only when no tokens
        are set, since nothing of the user's text can be found in it.
    """
    if not tokens:
        return True
    if relPath == ".":
        return False
    return text_matches(relPath, tokens, logic)
