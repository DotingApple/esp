from __future__ import annotations

import re

from coopt_agent.rtl_edit.whitelist import PatchError


def extract_port_block(source: str, module: str) -> str:
    """Return the parenthesised port list of `module`, verbatim.

    Verilog allows nested parentheses inside port declarations (vectors use
    brackets, but parameter expressions can nest), so the closing paren is found
    by counting depth rather than by matching the first ')'.
    """
    match = re.search(rf"\bmodule\s+{re.escape(module)}\b", source)
    if match is None:
        raise PatchError(f"module {module!r} not found in source")

    open_index = source.find("(", match.end())
    if open_index == -1:
        raise PatchError(f"module {module!r} has no port list")

    depth = 0
    for index in range(open_index, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
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
