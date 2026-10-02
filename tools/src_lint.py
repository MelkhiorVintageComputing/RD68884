#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The portability rules the three free front-ends do not enforce.

    python3 tools/src_lint.py rtl/*.sv

iverilog, Verilator and yosys all accept two things that Quartus, Vivado and
Questa do not, and both are in doc/coding-standard.md's table of measured quirks:

  use before declaration   a signal read or written above the line that declares
                           it. Quartus makes an implicit net of it and builds a
                           netlist that does not match the source, with exit code
                           0; Questa refuses.

  package name in a port   a package-scoped name, or the `UF macro that expands to
  connection               one, inside `.port ( ... )`. Quartus again: an implicit
                           net, and a wrong netlist.

Both were in the coding standard and both came back anyway, because the vendor
runs are not in `make check` and nothing cheaper looked. This is the cheaper
thing, and it is in `make lint`.

The declaration check is a scan, not a parser. It knows the declarations this
project writes -- `logic`, `wire`, `reg`, `bit`, `int`, `integer`, `genvar` and
`localparam`, at module level or in a generate block -- and treats every other
identifier on a line as a use. Its one blind spot is a name declared in one
generate branch and used in another, which is a different error and one every
front-end reports.
"""

import re
import sys

DECL = re.compile(
    r'^\s*(?:(?:input|output|inout)\s+)?'
    r'(?:logic|wire|reg|bit|int|integer|genvar|localparam|parameter)\b'
    r'(?:\s+(?:unsigned|signed|int|logic|bit))?'
    r'\s*((?:\[[^\]]*\]\s*)*)(.*)$')
IDENT = re.compile(r'(?<![\w$`.:\'])([A-Za-z_]\w*)')
PORT_PKG = re.compile(r'\.\w+\s*\([^;]*?(::|`UF\()')

KEYWORDS = set('''
    module endmodule input output inout logic wire reg bit int integer genvar
    localparam parameter assign always always_ff always_comb always_latch begin
    end if else case casez casex endcase unique priority default for generate
    endgenerate posedge negedge or and not function endfunction automatic
    return task endtask initial signed unsigned
'''.split())


def strip(text):
    text = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group(0).count('\n'), text,
                  flags=re.S)
    text = re.sub(r'//.*', '', text)
    return re.sub(r'"[^"\n]*"', '""', text)


def declared_names(rest):
    """Names a declaration's tail introduces: `a, b [0:3], c = x;`."""
    rest = rest.split(';')[0]
    names = []
    depth = 0
    token = ''
    for ch in rest + ',':
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            m = re.match(r'\s*([A-Za-z_]\w*)', token)
            if m:
                names.append(m.group(1))
            token = ''
        else:
            token += ch
    return names


def check_ports(path):
    """Package-scoped names inside `.port ( ... )`."""
    bad = []
    for n, line in enumerate(strip(open(path).read()).split('\n'), 1):
        if PORT_PKG.search(line):
            bad.append((n, 'package-scoped name in a port connection -- bind '
                           'it to a named signal (coding standard, Quartus)'))
    return bad


def check_order(path):
    """Uses above the declaration, with every module's declarations known."""
    bad = []
    lines = strip(open(path).read()).split('\n')
    modules = []
    start = None
    for n, line in enumerate(lines, 1):
        if re.match(r'^\s*module\b', line):
            start = n
        elif re.match(r'^\s*endmodule\b', line) and start:
            modules.append((start, n))
            start = None
    for lo, hi in modules:
        decl = {}
        header_end = lo
        for n in range(lo, hi + 1):
            if re.search(r'\)\s*;', lines[n - 1]):
                header_end = n
                break
        for n in range(header_end + 1, hi + 1):
            m = DECL.match(lines[n - 1])
            if m:
                for name in declared_names(m.group(2)):
                    decl.setdefault(name, n)
        for n in range(header_end + 1, hi + 1):
            line = lines[n - 1]
            if re.match(r'^\s*`define\b', line):
                continue                    # a macro body is text, not a use
            m = DECL.match(line)
            skip = set(declared_names(m.group(2))) if m else set()
            for u in IDENT.finditer(line):
                name = u.group(1)
                if name in KEYWORDS or name in skip:
                    continue
                at = decl.get(name)
                if at is not None and at > n:
                    bad.append((n, f"'{name}' is used here and declared at "
                                   f"line {at} -- declare it above its first use "
                                   f"(coding standard, Quartus/Vivado/Questa)"))
                    decl[name] = 0          # once per name is enough
    return bad


def main():
    failed = False
    for path in sys.argv[1:]:
        bad = check_ports(path) + check_order(path)
        for n, why in sorted(bad):
            print(f'  {path}:{n}: {why}')
            failed = True
    if failed:
        print('FAIL: source lint')
        return 1
    print(f'  source: {len(sys.argv) - 1} files, declared before use, '
          f'no package names in port connections')
    return 0


if __name__ == '__main__':
    sys.exit(main())
