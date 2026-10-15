"""The manual page: a banner, a synopsis and a colour scheme, rendered to a terminal.

A pf* command's ``--man`` and ``--synopsis`` print the same kind of page: ASCII-art
banner, title, prose, version, then NAME / SYNOPSIS / DESCRIPTION / ARGS / EXAMPLES.
This module renders such a page. On a terminal every line is set on a black
background padded to the terminal width, so the page reads the same on light and
dark terminals; piped output carries no escape codes and loses the ````code````
backticks of the prose.

Key components:
    - Manual: what a command's page is made of
    - manual_render: the page as text, coloured or plain
    - manual_print: render to stdout, colouring when it is a terminal
    - cliLine_highlight, inlineCode_render: the inline colouring rules
    - ManualAction, manual_attach: ``--man`` / ``--synopsis`` that print and exit while
      the command line is parsed, so they need none of the command's required arguments

Typical usage:
    MANUAL = Manual(command="pfdo", art=ART, title="Path-File Do", text=TEXT,
                    version=__version__, synopsisShort=short, synopsisFull=full)
    manual_print(MANUAL, shortOnly=False)
    manual_attach(parser, MANUAL)    # parser.parse_args(["--man"]) prints it and exits 0

Dependencies:
    - standard library only
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass

NO_COLOUR: str = "\033[0m"
BG_PAGE: str = "\033[40m"
FG_BODY: str = "\033[97m"
FG_BANNER: str = "\033[36m"
FG_VERSION: str = "\033[1;33m"
FG_HEADING: str = "\033[1;33m"
FG_COMMAND: str = "\033[1;32m"
FG_FLAG: str = "\033[36m"
FG_PLACEHOLDER: str = "\033[35m"
FG_STRING: str = "\033[33m"
FG_CONTINUATION: str = "\033[90m"
FALLBACK_WIDTH: int = 100

MANUAL_HEADING: re.Pattern[str] = re.compile(
    r"^\s{4}(NAME|SYNOPSIS|BRIEF EXAMPLE|DESCRIPTION|ARGS|EXAMPLES)\s*$"
)
INLINE_CODE: re.Pattern[str] = re.compile(r"``([^`]+)``")
BANNER_BULLET: re.Pattern[str] = re.compile(r"^(\s+)\*")
CLI_TOKEN_COLOURS: dict[str, str] = {
    "command": FG_COMMAND,
    "flag": FG_FLAG,
    "string": FG_STRING,
    "placeholder": FG_PLACEHOLDER,
    "continuation": FG_CONTINUATION,
}


@dataclass
class Manual:
    """What a command's manual page is made of.

    Attributes:
        command: the command's name, highlighted wherever it starts a CLI line.
        art: the ASCII-art banner.
        title: the one-line title under the art, set like a section heading.
        text: the banner prose; ``{version}`` in it is replaced by ``version``.
        version: the version string.
        synopsisShort: NAME, SYNOPSIS and a brief example.
        synopsisFull: the short synopsis followed by DESCRIPTION, ARGS and EXAMPLES.

    Example:
        >>> Manual("x", "", "X", "v {version}", "1.0", "s", "f").text_render()
        'v 1.0'
    """

    command: str
    art: str
    title: str
    text: str
    version: str
    synopsisShort: str
    synopsisFull: str

    def text_render(self) -> str:
        """The banner prose with the version filled in."""
        return self.text.format(version=self.version)

    def cliLine_pattern(self) -> re.Pattern[str]:
        """The lines that show command-line syntax, for this command."""
        return re.compile(rf"^\s{{8,}}({re.escape(self.command)}\b|\[--|--|<inputDir>|<outputDir>|/|\.\.\.)")

    def cliToken_pattern(self) -> re.Pattern[str]:
        """The tokens coloured inside a command-line line, for this command."""
        return re.compile(
            rf"(?P<command>\b{re.escape(self.command)}\b)"
            r"|(?P<flag>--[A-Za-z0-9]+)"
            r"|(?P<string>'[^']*')"
            r"|(?P<placeholder><[^<>]+>)"
            r"|(?P<continuation>\\$)"
        )


def page_width() -> int:
    """The terminal width the manual is padded to.

    Returns:
        The current terminal's column count, or a fallback when there is none.
    """
    return shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns


def line_onPage(coloured: str, visibleLength: int, width: int, foreground: str) -> str:
    """Set one line on the manual's black page.

    Args:
        coloured: the line with any inline colour codes already applied.
        visibleLength: the line's length without colour codes.
        width: the page width to pad to.
        foreground: the colour the line starts in.

    Returns:
        The line with the page background, padded to the width, reset at the end.
    """
    padding: str = " " * max(width - visibleLength, 0)
    return BG_PAGE + foreground + coloured + padding + NO_COLOUR


def cliLine_highlight(line: str, tokenPattern: re.Pattern[str]) -> str:
    """Colour a command-line example: command, flags, quoted strings, placeholders.

    Args:
        line: one line of a SYNOPSIS or EXAMPLES block.
        tokenPattern: the command's token pattern, from ``Manual.cliToken_pattern``.

    Returns:
        The line with each recognised token coloured and the body colour restored
        after it.
    """

    def token_colour(match: re.Match[str]) -> str:
        kind: str = str(match.lastgroup)
        return CLI_TOKEN_COLOURS[kind] + match.group(0) + FG_BODY

    return tokenPattern.sub(token_colour, line)


def inlineCode_render(line: str, colour: bool) -> str:
    """Replace ````code```` spans in prose with the bare text, coloured as a flag.

    Args:
        line: one prose line of the manual.
        colour: colour the span; otherwise only drop the backticks.

    Returns:
        The line with every span rewritten.
    """
    if colour:
        return INLINE_CODE.sub(lambda match: FG_FLAG + match.group(1) + FG_BODY, line)
    return INLINE_CODE.sub(lambda match: match.group(1), line)


def banner_render(manual: Manual, colour: bool, width: int = FALLBACK_WIDTH) -> str:
    """The title block of the manual.

    Args:
        manual: the page.
        colour: set the banner on the black page: the art cyan, the title yellow,
            the prose white, the version yellow, the bullets cyan.
        width: the page width when coloured.

    Returns:
        The banner text.
    """
    if not colour:
        return manual.art + manual.text_render()
    lines: list[str] = [line_onPage(line, len(line), width, FG_BANNER) for line in manual.art.splitlines()]
    for line in manual.text.splitlines():
        visible: str = line.replace("{version}", manual.version)
        if line.strip() == manual.title:
            lines.append(line_onPage(line, len(line), width, FG_HEADING))
        elif "{version}" in line:
            coloured: str = line.replace("{version}", FG_VERSION + manual.version + FG_BODY)
            lines.append(line_onPage(coloured, len(visible), width, FG_BODY))
        elif BANNER_BULLET.match(line):
            coloured = BANNER_BULLET.sub(lambda match: match.group(1) + FG_FLAG + "*" + FG_BODY, line)
            lines.append(line_onPage(coloured, len(visible), width, FG_BODY))
        else:
            lines.append(line_onPage(line, len(line), width, FG_BODY))
    return "\n".join(lines)


def synopsis_colourize(manual: Manual, text: str, width: int = FALLBACK_WIDTH) -> str:
    """Set the synopsis on a black page: white body, yellow headings, highlighted CLI.

    Args:
        manual: the page, for the command's patterns.
        text: the synopsis text.
        width: the page width.

    Returns:
        The coloured synopsis.
    """
    cliLine: re.Pattern[str] = manual.cliLine_pattern()
    tokens: re.Pattern[str] = manual.cliToken_pattern()
    page: list[str] = []
    for line in text.splitlines():
        if MANUAL_HEADING.match(line):
            page.append(line_onPage(line, len(line), width, FG_HEADING))
        elif cliLine.match(line):
            page.append(line_onPage(cliLine_highlight(line, tokens), len(line), width, FG_BODY))
        else:
            visible: str = inlineCode_render(line, colour=False)
            page.append(line_onPage(inlineCode_render(line, colour=True), len(visible), width, FG_BODY))
    return "\n".join(page)


def synopsis_plain(text: str) -> str:
    """The synopsis without colour and without the inline-code backticks.

    Args:
        text: the synopsis text.

    Returns:
        The text for a pipe or a file.
    """
    return "\n".join(inlineCode_render(line, colour=False) for line in text.splitlines())


def manual_render(manual: Manual, shortOnly: bool, colour: bool, width: int = FALLBACK_WIDTH) -> str:
    """The whole page as text.

    Args:
        manual: the page.
        shortOnly: the brief synopsis rather than the full one.
        colour: colour and pad every line; otherwise plain text.
        width: the page width when coloured.

    Returns:
        Banner and synopsis, joined with a newline.
    """
    synopsis: str = manual.synopsisShort if shortOnly else manual.synopsisFull
    if not colour:
        return banner_render(manual, colour=False) + "\n" + synopsis_plain(synopsis)
    return (
        banner_render(manual, colour=True, width=width) + "\n" + synopsis_colourize(manual, synopsis, width)
    )


def manual_print(manual: Manual, shortOnly: bool) -> None:
    """Print the page, coloured when stdout is a terminal.

    Args:
        manual: the page.
        shortOnly: print the brief synopsis rather than the full one.
    """
    colour: bool = sys.stdout.isatty()
    print(manual_render(manual, shortOnly, colour, page_width() if colour else FALLBACK_WIDTH))


class ManualAction(argparse._StoreTrueAction):
    """``--man`` or ``--synopsis``: print the attached manual and exit, as ``--version`` does.

    argparse checks required arguments only after it has read the whole command line,
    so a flag that is merely stored cannot spare a ChRIS plugin its ``inputdir
    outputdir``; one that acts as it is read can. With no manual attached it stores
    True like the plain flag it replaces, and the command handles it after parsing.
    It subclasses ``_StoreTrueAction`` because chris_plugin describes a flag to ChRIS
    by its action's class and refuses classes it does not know.

    Attributes:
        manual: the page to print; ``None`` until ``manual_attach`` sets it.
    """

    manual: Manual | None = None

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        if self.manual is None:
            super().__call__(parser, namespace, values, option_string)
            return
        manual_print(self.manual, shortOnly=self.dest == "synopsis")
        parser.exit(0)


def manual_attach(parser: argparse.ArgumentParser, manual: Manual) -> None:
    """Give a parser's ``--man`` and ``--synopsis`` the page they print.

    A parser built on another, as pfdo's on pftree's, attaches its own page over the
    one beneath it; the last attached is the one printed.

    Args:
        parser: a parser whose ``--man`` / ``--synopsis`` are ``ManualAction``.
        manual: the page.

    Example:
        >>> p = argparse.ArgumentParser(); _ = p.add_argument("--man", action=ManualAction)
        >>> manual_attach(p, Manual("x", "", "X", "", "1", "short", "full"))
        >>> p.parse_args(["--man"])
        Traceback (most recent call last):
        SystemExit: 0
    """
    # argparse keeps a parser's actions in the private _actions list; chris_plugin
    # reads the same list, and it is stable from Python 3.11 to 3.14.
    for action in parser._actions:
        if isinstance(action, ManualAction):
            action.manual = manual
