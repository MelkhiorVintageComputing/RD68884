#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Prove that no register in rtl/ initialises outside reset.

    python3 tools/reset_audit.py --top rd68884_top --build build rtl/*.sv

ASIC is a target, so there is no power-on register state. The rule (CLAUDE.md) is
that every register takes its value from the reset branch of its own `always_ff`,
and that nothing in rtl/ contains an `initial` block, a declaration-site initialiser
on anything that infers a register, or a latch.

Two checks, because either one alone can be fooled:

  source     a scan for the constructs the rule forbids. Catches intent, and names
             the file and line, but cannot see what the tool actually inferred.

  netlist    yosys to bit-level cells, then a count of flip-flop cell types. A
             register with an asynchronous reset is a different cell from one
             without -- $_DFF_PN0_ against $_DFF_P_ -- so a flop that escaped the
             rule is a cell type that should not be there. This is the check that
             cannot be argued with.

The netlist check deliberately stops at `proc; flatten; simplemap` and does NOT run
`synth`. Optimisation deletes registers whose outputs nothing reads, which on a
partly-built design is most of them -- running the full flow here reported "0
flip-flops, every one reset", which is true and worthless. Stopping before
`opt_clean` audits every register the source actually describes.

Memories are not registers. yosys leaves an inferred RAM as a $mem cell, which
this audit does not count. The register file is one: every FP register and
microcode temporary is written by the reset microcode (FRESTORE of a null frame,
MC68881UM 6.4.2.1, does the same) before anything reads it.

Exemptions are named individually in EXEMPT below, with the argument for each, and
the audit fails if a second one appears. A blanket allowance would defeat the point.
"""

import argparse
import os
import re
import subprocess
import sys

# Flip-flop cell types yosys emits that carry a reset. Anything here is fine.
RESET_FF = {
    '$_DFF_PN0_', '$_DFF_PN1_', '$_DFF_NN0_', '$_DFF_NN1_',
    '$_DFF_PP0_', '$_DFF_PP1_', '$_DFF_NP0_', '$_DFF_NP1_',
    '$_SDFF_PN0_', '$_SDFF_PN1_', '$_SDFF_NN0_', '$_SDFF_NN1_',
    '$_SDFF_PP0_', '$_SDFF_PP1_', '$_SDFF_NP0_', '$_SDFF_NP1_',
    '$_DFFSR_PNN_', '$_DFFSR_NNN_',
}

# Flip-flop and latch cell types that carry no reset. Anything here is a failure
# unless it is named in EXEMPT.
UNRESET_FF = {
    '$_DFF_P_', '$_DFF_N_',
    '$_DFFE_PP_', '$_DFFE_PN_', '$_DFFE_NP_', '$_DFFE_NN_',
    '$_DLATCH_P_', '$_DLATCH_N_',
    '$_SR_PP_', '$_SR_PN_', '$_SR_NP_', '$_SR_NN_',
}

# Registers allowed to exist without a reset, each with the argument for it. The
# audit fails if the netlist holds an unreset flop that is not on this list, and it
# also fails if a name here stops being needed -- an exemption that has quietly
# become unnecessary is one nobody will re-examine.
#
# None yet. The microcode store's read register will be the first (as in
# RD68021): a block RAM keeps it inside the primitive and cannot give it a reset
# value on every part, and it is reset-equivalent because its address is forced
# to the reset entry point while rst_n is low.
EXEMPT = {}

FORBIDDEN = [
    (re.compile(r'^\s*initial\b'),
     "`initial` block -- ASIC has no power-on state"),
    (re.compile(r'^\s*always_latch\b'),
     "`always_latch` -- a latch has no reset"),
    # \b after the keyword: without it `regn = ...` inside a process reads as
    # `reg n = ...`, a declaration with an initialiser.
    (re.compile(r'^\s*(?:logic|reg|bit)\b\s*(?:\[[^]]*\]\s*)*[A-Za-z_]\w*\s*='),
     "declaration-site initialiser -- give it a value in the reset branch instead"),
]


def strip_comments(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//.*', '', text)


def scan_source(files):
    """The constructs the rule forbids, by file and line."""
    bad = []
    for path in files:
        with open(path) as fh:
            raw = fh.read()
        # Comment out what is inside comments, keeping line numbers.
        cleaned = []
        for line in strip_comments(raw).split('\n'):
            cleaned.append(line)
        for n, line in enumerate(cleaned, 1):
            for pattern, why in FORBIDDEN:
                if pattern.search(line):
                    bad.append((path, n, why, line.strip()))
    return bad


def yosys_netlist(files, top, build, params=()):
    """Elaborate to bit-level cells and return {cell type: count}.

    `proc` turns always blocks into word-level $dff/$adff, `flatten` pulls the whole
    hierarchy into one module so a register is counted once, and `simplemap` breaks
    the word-level cells into the bit-level $_DFF_* types whose names say whether
    they carry a reset. Nothing that deletes cells is run -- see the note above.
    """
    out = os.path.join(build, f'{top}_audit.v')
    chparam = ''.join(f"chparam -set {k} {v} {top}; "
                      for k, v in (p.split('=', 1) for p in params))
    script = (f"read_verilog -sv {' '.join(files)}; "
              f"{chparam}"
              f"hierarchy -check -top {top}; "
              f"proc; flatten; "
              # proc_memwr gives a RAM's write port its own clock and then
              # leaves behind the three registers proc staged the port's
              # address, data and enable in, driving wires named $memwr$...
              # that nothing reads. They are not state: delete exactly those
              # and no other cell -- opt_clean would do it too, but it would
              # also delete every genuine register nothing reads yet.
              f"delete w:*$memwr$* %ci1:+$dff[Q] t:$dff %i; "
              f"simplemap; "
              f"stat; write_verilog {out}")
    proc = subprocess.run(['yosys', '-p', script], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit('reset_audit: yosys failed')

    counts = {}
    in_stat = False
    for line in proc.stdout.split('\n'):
        if line.startswith('=== ') and line.rstrip().endswith(' ==='):
            in_stat = (line.strip() == f'=== {top} ===')
            continue
        if not in_stat:
            continue
        m = re.match(r'\s+(\$_\w+_)\s+(\d+)\s*$', line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + int(m.group(2))
    return counts, out


def source_has_registers(files):
    """Does the source describe any clocked storage at all?

    If it does and the netlist has no flip-flops, the netlist check proved nothing
    and must say so rather than pass.
    """
    for path in files:
        if re.search(r'^\s*always_ff\b', strip_comments(open(path).read()),
                     flags=re.M):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', default='rd68884_top')
    ap.add_argument('--build', default='build')
    ap.add_argument('--param', action='append', default=[],
                    help='NAME=VALUE, a top-level parameter to audit the design at')
    ap.add_argument('--source-only', action='store_true',
                    help='skip the netlist check, for a fast pass')
    ap.add_argument('files', nargs='+')
    args = ap.parse_args()

    failed = False

    bad = scan_source(args.files)
    if bad:
        failed = True
        print('FAIL: audit -- source')
        for path, line, why, text in bad:
            print(f'  {path}:{line}: {why}')
            print(f'      {text}')
    else:
        print(f'  source: {len(args.files)} files, no initialisation outside reset')

    if not args.source_only:
        os.makedirs(args.build, exist_ok=True)
        counts, _ = yosys_netlist(args.files, args.top, args.build,
                                  args.param)

        reset = sum(n for c, n in counts.items() if c in RESET_FF)
        unreset = {c: n for c, n in counts.items() if c in UNRESET_FF}
        unknown = {c: n for c, n in counts.items()
                   if c not in RESET_FF and c not in UNRESET_FF
                   and ('DFF' in c or 'LATCH' in c or c.startswith('$_SR'))}

        if unknown:
            failed = True
            print('FAIL: audit -- netlist holds a state cell this audit does not '
                  'classify, so it cannot say whether it is reset:')
            for c, n in sorted(unknown.items()):
                print(f'  {c}: {n}')
            print('  Add it to RESET_FF or UNRESET_FF in tools/reset_audit.py.')

        allowed = sum(EXEMPT.values())
        found = sum(unreset.values())
        if found > allowed:
            failed = True
            print(f'FAIL: audit -- {found} flip-flops or latches initialise outside '
                  f'reset, {allowed} exempted:')
            for c, n in sorted(unreset.items()):
                print(f'  {c}: {n}')
        elif found < allowed:
            failed = True
            print(f'FAIL: audit -- {allowed} registers are exempted but only {found} '
                  f'unreset cells exist. An exemption that is no longer needed is one '
                  f'nobody will re-examine; remove it from EXEMPT.')
        elif reset == 0 and source_has_registers(args.files):
            failed = True
            print('FAIL: audit -- the source contains always_ff blocks but the '
                  'netlist has no flip-flops, so this check proved nothing. '
                  'Something in the yosys flow is deleting them.')
        else:
            breakdown = ', '.join(f'{n}x {c}' for c, n in sorted(counts.items())
                                  if c in RESET_FF)
            print(f'  netlist: {reset} flip-flops, every one reset'
                  + (f', {allowed} exempted' if allowed else ''))
            print(f'           {breakdown}')

    if failed:
        return 1
    print('PASS: audit')
    return 0


if __name__ == '__main__':
    sys.exit(main())
