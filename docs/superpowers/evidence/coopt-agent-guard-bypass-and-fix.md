# coopt_agent review fix wave — report

Date: 2026-09-17
Branch: `coopt`
Test command: `/home/pd2827/esp/soc_opt_agent/.venv/bin/pytest` from `/home/pd2827/esp/coopt_agent`

Start: 50 passing. End: **77 passing**. Nothing under `accelerators/`, `socs/`,
`tech/` or `soft/` was modified; the real wrapper and the RTL tree are read only.

## Commits

| SHA | Subject |
|---|---|
| `288ea6ce` | coopt_agent: close the comment bypass in the port guard |
| `b9198925` | coopt_agent: compose the guards, and pin the patch path convention |
| `d41f6acb` | coopt_agent: drift test for the editable-file whitelist |
| `cae7513b` | coopt_agent: serialisable derivation, and no bare ValueError from it |

---

## FINDING 1 — CRITICAL. Port guard defeated by a comment the patch inserts

**Failing test written first:**
`tests/test_rtl_edit.py::test_decoy_comment_cannot_smuggle_a_port_removal_past_the_guard`

It builds the two-edit proposal from the finding: edit 1 inserts a block comment
above the real `module lstm_rtl_basic_dma64` holding a decoy declaration and a
verbatim copy of the baseline port list; edit 2 deletes `acc_done` from the real
port list, anchored on `*/` + the real head, which the decoy does not contain, so
guard 2's uniqueness check is satisfied.

**Observed failure (pre-fix):** `Failed: DID NOT RAISE PatchError` — the guard
read the decoy, compared it byte-for-byte against the baseline, found it
identical, and passed a wrapper whose real interface no longer had `acc_done`.
All three guards green on a patch that removes the signal the SoC waits on.

**Fix** (`src/coopt_agent/rtl_edit/guard.py`):

- New `blank_comments(source)` replaces every `//`-to-EOL and `/* */` comment
  with spaces of the **same length** (newlines inside block comments preserved),
  so all offsets survive.
- `_find_declaration` runs `re.finditer(r"\bmodule\s+<name>\b")` over the blanked
  copy. Zero matches → `PatchError` (as before). **More than one → `PatchError`**:
  fail closed rather than take the first, since picking one would be a guess
  about which module carries the ESP interface.
- `extract_port_block` counts parenthesis depth on the blanked copy and then
  slices the **original** source at those offsets, so `assert_ports_unchanged`
  still sees comments and formatting exactly as written and its byte-for-byte
  comparison is unchanged.

**Tests now covering it** (all passing):

- `test_decoy_comment_cannot_smuggle_a_port_removal_past_the_guard` — the bypass
  above now raises `PatchError`.
- `test_line_comment_naming_the_module_is_ignored` — a `//` line naming the
  module before the real declaration; extraction matches the clean source.
- `test_block_comment_naming_the_module_is_ignored` — same for `/* */`, with a
  full decoy port list inside.
- `test_two_real_declarations_of_the_same_module_are_rejected` — a file genuinely
  declaring the module twice now raises.
- `test_extract_port_block_on_the_real_wrapper` — see the last minor item below.

## FINDING 2 — `apply_edits` admitted ambiguous overlapping matches

**Failing test written first:**
`tests/test_rtl_edit.py::test_overlapping_matches_are_rejected`

Source `"            end\n            end\n            end\n"`, `old_text` the
first two of those lines. **Observed failure (pre-fix):** `DID NOT RAISE` —
`str.count` counts non-overlapping occurrences, so two real match positions (0
and 16) counted as 1, and the edit was applied at the first: the guess spec §8.2
forbids.

**Fix** (`src/coopt_agent/rtl_edit/patch.py`): new `_count_positions`, a
`str.find(old, position + 1)` loop counting every start position including
overlapping ones. `apply_edits` uses it in place of `str.count`; the
"exactly 1" rule and its message are unchanged, so the existing zero-match and
two-match tests still pass.

## FINDING 3 — `rtl_patch.file`'s path convention was undefined

**Ruling implemented: `rtl_patch.file` carries the ACCELERATOR-RELATIVE path.**

**Fix** (`src/coopt_agent/proposal/schema.py`): a class docstring on `RtlPatch`
states the convention plainly with the concrete example
(`hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v`), contrasts it against the
repo-relative spelling, and gives the reason: the whitelist is per-accelerator,
so converting a repo-relative path would need the accelerator root, which the
schema does not carry. That normalization would land in the orchestrator, where
the obvious implementation is fuzzy suffix matching — and a suffix match turns an
allowlist that currently fails closed into one that can be talked past.

**Pinning test:** `tests/test_rtl_edit.py::test_repo_relative_path_is_rejected`
asserts `check_target` rejects
`accelerators/rtl/lstm_rtl/hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v`.
This one passed on first run by construction — it documents behaviour that was
already there but only implied, which is the point of the finding. The spec
document was **not** edited, as instructed.

## FINDING 4 — nothing composed the guards in the mandated order

**Failing test written first:** the five `test_apply_patch_*` tests in
`tests/test_rtl_edit.py`. **Observed failure (pre-fix):**
`ImportError: cannot import name 'apply_patch' from 'coopt_agent.rtl_edit'` —
collection of the whole module failed.

**Fix** (new `src/coopt_agent/rtl_edit/apply.py`, exported from
`rtl_edit/__init__.py`):

```python
def apply_patch(whitelist: Whitelist, source: str, patch: RtlPatch) -> str
```

Runs `check_target(whitelist, patch.file)`, then `apply_edits(source,
patch.edits)`, then `assert_ports_unchanged(source, patched,
whitelist.top_module)`, returning the patched text only if all three pass. It
never touches the filesystem, so a rejected candidate cannot leave a
half-patched tree behind and no build can be attempted on one.

Tests: each guard's failure propagates as `PatchError`
(`..._rejects_a_non_editable_file`, `..._rejects_an_ambiguous_edit`,
`..._rejects_a_port_change`), the happy path returns patched text, and
`test_apply_patch_returns_nothing_on_failure` checks a sentinel is left
unrebound on failure.

## FINDING 5 — the whitelist had no drift test

**New file:** `coopt_agent/tests/test_whitelist_matches_sources.py`, seven tests,
parametrized over every accelerator in `configs/editable_files.yaml`. For each it
READS the real files under `accelerators/rtl/<name>/` and asserts:

- the accelerator directory exists;
- every listed `editable` and `read_only` path exists;
- at least one editable file is listed;
- `top_module` is actually declared in an editable file (otherwise guard 3 could
  never run on a real patch);
- each `read_only` file declares at least one module that is **not** the top
  module (that is what makes it compute core, and what D1 protects);
- `editable` and `read_only` do not overlap.

Module names are extracted through `blank_comments`, so a commented-out `module`
line cannot satisfy the drift test either.

It writes nothing. It passed on first run, as a drift test against a correct
config should, so its teeth were checked separately by mutating the loaded
whitelist in memory: a missing path, a wrong `top_module`, a `read_only` entry
that holds only the wrapper, and an editable/read_only overlap each produced the
expected assertion failure.

## MINOR fixes

**Paren inside a port comment.** Covered by the Finding 1 comment blanking.
Two tests: `test_paren_inside_a_port_comment_does_not_truncate_the_block`
(`// active-low :-)` — pre-fix the `)` closed the depth count early and the
extracted block stopped mid-port-list, hiding any later port change from the
guard) and `test_open_paren_inside_a_port_comment_does_not_swallow_the_body`
(`// active-low :-(` — pre-fix the unbalanced `(` ran the depth scan into the
module body, so a legitimate body-only patch was falsely rejected as a port
change). Both failed before the fix, both pass now.

**Bare `ValueError` from `derive.py`.** Failing tests first:
`test_non_numeric_baseline_scalar_raises_legality_error` (baseline
`CONFIG_QUEUE_SIZE: "default"`) and
`test_non_numeric_baseline_composite_field_raises_legality_error` (baseline
`CONFIG_ACC_CACHES: "auto 4"`). Both raised a bare `ValueError` before the fix.
New `_as_int(key, raw, field_name=...)` wraps both the scalar and composite
parses and raises `LegalityError` naming the dotted path — a junk baseline is a
statement about the configuration, not a Python error, and callers here catch
`LegalityError`.

**`Derivation` not JSON-serializable (spec §9's `soc_derivation.json`).**
Failing tests first: `test_derivation_to_dict_is_json_serialisable`,
`test_derivation_to_dict_has_no_clamps_when_nothing_was_clamped`,
`test_derivation_to_dict_does_not_alias_the_config` — all
`AttributeError: 'Derivation' object has no attribute 'to_dict'`. Added
`Derivation.to_dict()` returning `{"config": {...}, "clamps": [{"knob",
"requested", "applied"}, ...]}`, plain types only, copies not aliases. The clamp
records are the part that matters: they record that a requirement was *not*
fully met, and without them a rerun cannot tell a config that satisfied every
requirement from one that hit the legal ceiling.

**Two requirements on different fields of one composite key.** Added
`test_two_requirements_on_different_fields_of_one_composite_key`
(`CONFIG_ACC_CACHES.acc_l2_sets` = 2048 and `.acc_l2_ways` = 8 → `"2048 8"`).
Passed on first run — the behaviour was already correct; this pins it against a
future refactor that merged on the bare key or rebuilt the composite string from
the baseline rather than from the running value.

## Explicitly not done, as instructed

- `read_only` is still not consulted by `check_target`.
- `KeyError`/`IndexError` from the config loaders are still unwrapped.
- No `CONFIG_CACHE_EN` prerequisite check in `derive_config`.
- No cross-field legality constraints.
- The spec document was not edited.

## Final full-suite run

```
$ /home/pd2827/esp/soc_opt_agent/.venv/bin/pytest -v
============================= test session starts ==============================
platform linux -- Python 3.12.11, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/pd2827/esp/coopt_agent
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 77 items

tests/test_legality_mirrors_socgen.py ............                       [ 15%]
  (11 knob-mirror cases + test_no_extra_knobs)
tests/test_proposal.py .........                                         [ 27%]
tests/test_rtl_edit.py .............................                     [ 64%]
  including:
    test_decoy_comment_cannot_smuggle_a_port_removal_past_the_guard PASSED
    test_line_comment_naming_the_module_is_ignored                 PASSED
    test_block_comment_naming_the_module_is_ignored                PASSED
    test_two_real_declarations_of_the_same_module_are_rejected     PASSED
    test_paren_inside_a_port_comment_does_not_truncate_the_block   PASSED
    test_open_paren_inside_a_port_comment_does_not_swallow_the_body PASSED
    test_extract_port_block_on_the_real_wrapper                    PASSED
    test_overlapping_matches_are_rejected                          PASSED
    test_repo_relative_path_is_rejected                            PASSED
    test_apply_patch_returns_patched_text_when_all_guards_pass     PASSED
    test_apply_patch_rejects_a_non_editable_file                   PASSED
    test_apply_patch_rejects_an_ambiguous_edit                     PASSED
    test_apply_patch_rejects_a_port_change                         PASSED
    test_apply_patch_returns_nothing_on_failure                    PASSED
tests/test_soc_relax.py ....................                             [ 90%]
  including:
    test_two_requirements_on_different_fields_of_one_composite_key PASSED
    test_non_numeric_baseline_scalar_raises_legality_error         PASSED
    test_non_numeric_baseline_composite_field_raises_legality_error PASSED
    test_derivation_to_dict_is_json_serialisable                   PASSED
    test_derivation_to_dict_has_no_clamps_when_nothing_was_clamped PASSED
    test_derivation_to_dict_does_not_alias_the_config              PASSED
tests/test_whitelist_matches_sources.py .......                          [100%]

============================== 77 passed in 0.17s ==============================
```

All 50 pre-existing tests still pass; 27 added.
