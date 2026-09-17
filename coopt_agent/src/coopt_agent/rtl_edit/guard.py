from __future__ import annotations

import re

from coopt_agent.rtl_edit.whitelist import PatchError

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def blank_comments(source: str) -> str:
    """Return `source` with every comment replaced by spaces of equal length.

    Offsets are preserved exactly, so a match found in the blanked copy can be
    used to slice the *original* text. Newlines inside block comments are kept
    so line structure survives.

    This is what makes the module search trustworthy. A proposal can otherwise
    insert a block comment above the real declaration containing a decoy
    `module <top>` and a verbatim copy of the baseline port list, then delete a
    port from the real one: the guard reads the decoy, compares it against the
    baseline, finds it identical, and passes an interface that has lost the
    signal the SoC waits on. Blanking also stops a stray parenthesis inside a
    port comment (`// active-low :-)`) from truncating the extracted block or
    running the depth scan into the module body.
    """

    def _blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    return _COMMENT.sub(_blank, source)


def _find_declaration(source: str, module: str) -> int:
    """Return the end offset of the sole uncommented declaration of `module`."""
    blanked = blank_comments(source)
    matches = list(re.finditer(rf"\bmodule\s+{re.escape(module)}\b", blanked))
    if not matches:
        raise PatchError(f"module {module!r} not found in source")
    if len(matches) > 1:
        # Fail closed. Picking the first would be a guess about which module
        # the SoC instantiates, and spec section 8 never guesses.
        raise PatchError(
            f"module {module!r} is declared {len(matches)} times in this "
            "source; refusing to guess which one carries the ESP interface"
        )
    return matches[0].end()


def extract_port_block(source: str, module: str) -> str:
    """Return the parenthesised port list of `module`, verbatim.

    The declaration is located, and the parenthesis depth counted, in a copy of
    the source with comments blanked out; the text returned is sliced from the
    original source, so the byte-for-byte comparison in
    `assert_ports_unchanged` still sees comments and formatting exactly as
    written.

    Verilog allows nested parentheses inside port declarations (vectors use
    brackets, but parameter expressions can nest), so the closing paren is
    found by counting depth rather than by matching the first ')'.
    """
    blanked = blank_comments(source)
    search_from = _find_declaration(source, module)

    open_index = blanked.find("(", search_from)
    if open_index == -1:
        raise PatchError(f"module {module!r} has no port list")

    depth = 0
    for index in range(open_index, len(blanked)):
        if blanked[index] == "(":
            depth += 1
        elif blanked[index] == ")":
            depth -= 1
            if depth == 0:
                return source[open_index : index + 1]
    raise PatchError(f"module {module!r} has an unterminated port list")


def assert_ports_unchanged(before: str, after: str, module: str) -> None:
    """Spec section 8 guard 3: the ESP interface contract is not negotiable.

    Compared byte-for-byte. Whitespace inside the port list is not normalised:
    a patch that reformats the interface is still a patch that touched it, and
    this guard is cheap insurance against protocol changes that compile fine and
    then hang the SoC in simulation.
    """
    if extract_port_block(before, module) != extract_port_block(after, module):
        raise PatchError(
            f"the patch changed the port list of module {module!r}; the ESP "
            "interface must stay byte-for-byte identical"
        )
