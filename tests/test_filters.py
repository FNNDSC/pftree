"""Tests for the filter token grammar."""

from __future__ import annotations

import pytest

from pftree.filters import FilterLogic, FilterToken, dirPath_matches, fileNames_filter, tokens_parse


def test_logic_parse_isCaseInsensitive() -> None:
    assert FilterLogic.parse("and") is FilterLogic.AND
    assert FilterLogic.parse(" Or ") is FilterLogic.OR


def test_logic_parse_rejectsOtherWords() -> None:
    with pytest.raises(ValueError, match="AND or OR"):
        FilterLogic.parse("XOR")


def test_token_parse_kinds() -> None:
    assert tokens_parse("nii") == [FilterToken("substring", "nii")]
    assert tokens_parse("glob:*.nii.gz") == [FilterToken("glob", "*.nii.gz")]
    assert tokens_parse("re:^sub-\\d+$") == [FilterToken("regex", "^sub-\\d+$")]
    assert tokens_parse("!tmp") == [FilterToken("substring", "tmp", negate=True)]
    assert tokens_parse("!glob:*.bak") == [FilterToken("glob", "*.bak", negate=True)]


def test_token_parse_unknownPrefixIsSubstring() -> None:
    assert tokens_parse("ext:nii") == [FilterToken("substring", "ext:nii")]


def test_token_parse_emptySpecIsNoTokens() -> None:
    assert tokens_parse("") == []
    assert tokens_parse("   ") == []


def test_token_parse_rejectsEmptyToken() -> None:
    with pytest.raises(ValueError, match="empty"):
        tokens_parse("nii,,gz")


def test_token_parse_rejectsBadRegex() -> None:
    with pytest.raises(ValueError, match="bad regular expression"):
        tokens_parse("re:(")


def test_substring_matchesAnywhere() -> None:
    token: FilterToken = FilterToken("substring", "nii")
    assert token.matches("a.nii.gz")
    assert token.matches("niix")
    assert not token.matches("a.mgz")


def test_glob_anchorsWholeName() -> None:
    token: FilterToken = FilterToken("glob", "*.nii.gz")
    assert token.matches("a.nii.gz")
    assert not token.matches("a.nii.gz.bak")
    assert not token.matches("a.niix")


def test_regex_searches() -> None:
    token: FilterToken = FilterToken("regex", "^sub-\\d+$")
    assert token.matches("sub-01")
    assert not token.matches("xsub-01")


def test_negation_flipsVerdict() -> None:
    assert not FilterToken("substring", "tmp", negate=True).matches("tmp.txt")
    assert FilterToken("substring", "tmp", negate=True).matches("keep.txt")


def test_fileNames_filter_orKeepsAnyHit() -> None:
    names: list[str] = ["b.png", "a.jpg", "c.txt"]
    assert fileNames_filter(names, tokens_parse("png,jpg"), FilterLogic.OR) == ["a.jpg", "b.png"]


def test_fileNames_filter_andNeedsEveryHit() -> None:
    names: list[str] = ["aparc.mgz", "aparc.txt", "brain.mgz"]
    assert fileNames_filter(names, tokens_parse("aparc,mgz"), FilterLogic.AND) == ["aparc.mgz"]


def test_fileNames_filter_noTokensKeepsAllSorted() -> None:
    assert fileNames_filter(["b", "a"], [], FilterLogic.OR) == ["a", "b"]


def test_fileNames_filter_negationWithAnd() -> None:
    names: list[str] = ["a.nii.gz", "tmp.nii.gz"]
    kept: list[str] = fileNames_filter(names, tokens_parse("glob:*.nii.gz,!tmp"), FilterLogic.AND)
    assert kept == ["a.nii.gz"]


def test_dirPath_matches_substringOnRelativePath() -> None:
    tokens = tokens_parse("100307,100556")
    assert dirPath_matches("sub/100307/anat", tokens, FilterLogic.OR)
    assert dirPath_matches("100556", tokens, FilterLogic.OR)
    assert not dirPath_matches("sub/999/anat", tokens, FilterLogic.OR)


def test_dirPath_matches_rootNeverMatchesTokens() -> None:
    assert dirPath_matches(".", [], FilterLogic.OR)
    assert not dirPath_matches(".", tokens_parse("."), FilterLogic.OR)


def test_dirPath_matches_regexAnchorsOnComponent() -> None:
    tokens = tokens_parse("re:/anat$")
    assert dirPath_matches("sub-01/anat", tokens, FilterLogic.OR)
    assert not dirPath_matches("sub-01/anatomy", tokens, FilterLogic.OR)
    assert not dirPath_matches("anat", tokens, FilterLogic.OR)


def test_dirPath_matches_globOverRelativePath() -> None:
    tokens = tokens_parse("glob:*/anat")
    assert dirPath_matches("sub-01/ses-1/anat", tokens, FilterLogic.OR)
    assert not dirPath_matches("anat", tokens, FilterLogic.OR)


def test_dirPath_matches_andAcrossTokens() -> None:
    tokens = tokens_parse("sub-01,anat")
    assert dirPath_matches("sub-01/anat", tokens, FilterLogic.AND)
    assert not dirPath_matches("sub-02/anat", tokens, FilterLogic.AND)
