# RD68884 coding standard

The RTL must elaborate under **iverilog 12.0, Verilator 5.032, yosys 0.52, Vivado 2025.2,
Quartus Prime Lite and Questa Altera Starter Edition**. That intersection, not the
SystemVerilog LRM, is the language this project is written in.

`make lint` runs the first three, which is why they are the ones the design is written
against day to day. The other three need a vendor installation, so each has its own
target: `make synth` for Vivado, `make lint-quartus` for Quartus, `make lint-questa` for
Questa. None of the three is in `make check`, which has to work on a machine with neither
vendor installed.

Everything below marked *(measured)* was established by trying it — on this machine, or
on the same machine during the MC68010 project this standard is inherited from. Nothing
here is assumed.

This standard is inherited unchanged from RD68021 (the companion MC68020,
`../RD68021/doc/coding-standard.md`), where every rule below was measured. Names
are adapted to this project: modules are `rd68884_<unit>`, the waiver file is
`rtl/rd68884.vlt`. Two differences from RD68021 are worth stating up front:

- **One clock edge.** RD68021 is dual-edge because the MC68020's bus states are
  half clocks. The FPU's bus is asynchronous, so RD68884 is a single
  `posedge clk_core` design, and its input synchronisers are posedge too.
  The dual-edge notes below are kept because they are measured facts, not
  because this design uses them.
- **Citations** in comments use `FPU` for `MC68881UM_split` (e.g. "FPU 7.4.2")
  and `UM` for `MC68020UM_split`. Specification numbers refer to
  `MC68881UM_split/ac-electrical-specifications.csv`.

## The subset

**yosys is the constraint.** It is markedly stricter than the other three, so it decides
what the RTL may use.

### Forbidden — yosys rejects it *(measured)*

| Construct | What happens |
|---|---|
| `import pkg::*;` in a module header | `ERROR: syntax error, unexpected TOK_ID` |
| `import pkg::*;` in the module body | `ERROR: syntax error, unexpected TOK_PACKAGESEP` |
| `import pkg::name;` | same |

There is no working form of `import`. **Refer to package members with their full scope
every time**: `rd68884_pkg::FC_CPU`, `rd68884_pkg::bus_state_e`. It is verbose; it is also
unambiguous about where a constant came from, which for a design transcribed out of a
manual is worth something.

### Allowed — every tool accepts it *(measured)*

- `package` / `endpackage` with `localparam`, `typedef enum`, `typedef struct packed`
- enum and packed-struct **variables** inside modules, including struct field assignment
  (`r.a <= 4'd1`) and enum comparison
- `always_ff` / `always_comb`, `unique case`, `logic`, `'0` fill
- **Dual-edge design**: a `posedge clk` block and a `negedge clk` block in the same module.
  yosys infers `$_DFF_PN0_` and `$_DFF_NN0_` correctly. This matters — the whole bus-state
  scheme depends on it.
- Asynchronous active-low reset in the sensitivity list
- A **posedge flop and a negedge flop combined with XOR** to make an output that changes
  on both edges (`rd68021_dedge_ff`). Only one side can change at any instant, so the
  result is glitch-free, and every tool infers it correctly. `ECS` and `OCS`, which are
  asserted for exactly one half clock, are built from it.

### Forbidden — project rules rather than tool limits

| Rule | Why |
|---|---|
| No `initial` blocks in `rtl/` | ASIC has no power-on state |
| No initialisers on register declarations | same |
| No `assert property` / SVA in `rtl/` | iverilog and yosys do not support it |
| No interfaces, classes, queues, dynamic arrays, `unions` | not portable |
| No `$random`, `$display` in `rtl/` | not synthesisable |
| No non-constant loop bounds | not synthesisable |
| No user types on module **ports** | keeps yosys and cross-tool elaboration happy; use plain `logic [N:0]` and convert inside |
| No inline `lint_off` pragmas | waivers go in `rtl/rd68884.vlt` with a reason |
| **No function that reads module state, called from a continuous assignment** | see below — the tools disagree about when it is re-evaluated |

Testbenches under `sim/` are not bound by these: they only ever run under iverilog or
Verilator, and may use `initial`, `$display`, tasks, `realtime` and the rest.

## Reset

Every register is reset. There is no exception and no "don't care" register.

```systemverilog
always_ff @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin
    q <= '0;          // explicit value for every register in the block
  end else begin
    q <= d;
  end
end
```

`rst_n` is **not** an MC68020 pin. It is the hardware/simulation initialisation input that
gives every flop a defined value; the architectural reset behaviour (RESET asserted, the
ISP fetched from $0 and the PC from $4) is a separate sequence driven by `reset_n_i`. Do
not conflate them.

The instruction cache is the one structure that looks like an exception and is not. UM
§4.2: "during processor reset, the cache is cleared by resetting all of the valid bits."
The valid bits are resettable flops; the tag and data arrays are an unreset RAM that the
valid bits gate. Nothing reads an uninitialised bit.

## Naming

| Suffix | Meaning |
|---|---|
| `_n` | active low **at the pin** — `as_n_o == 0` means AS asserted |
| `_i` | input pin |
| `_o` | output pin |
| `_oe` | output enable, **active high = core is driving** |
| `_q` | registered version of a combinational signal, where both exist |
| `_nxt` | the combinational next-state value of a register |
| `_e` | enum type name (`bus_state_e`) |
| `_t` | struct type name |

Modules are `rd68884_<unit>` in `rtl/rd68884_<unit>.sv`, one module per file. Instances
are `u_<name>`. Package members are `UPPER_SNAKE` for constants and `UPPER` for enum
values.

## Comments

Cite the source. A line of RTL that implements something from the manual says which
section it came from:

```systemverilog
// UM 5.1.1: SIZ1/SIZ0 indicate the number of bytes REMAINING to be transferred,
// not the size of the original operand. Specification 6 measures them from CLK
// high, so they live in the positive-edge domain.
```

`UM` is `MC68020UM_split`, `PRM` is `M68000PRM_split`. Specification numbers refer to
`ac-electrical-specifications.csv`. This is not decoration: when a behaviour is later
questioned, the citation is what settles it.

## Lint waivers

Waivers live in `rtl/rd68884.vlt`, each with a written reason. Every module carries an
explicit sink naming what it deliberately does not read:

```systemverilog
logic unused_x;
assign unused_x = &{1'b1, some_input, another_input};
```

Three file-format gotchas, all hit in practice *(measured)*: `` `verilator_config `` must
be the first token in a `.vlt` file; waiver globs are matched against the path as given on
the command line, so `*rtl/foo.sv` matches and `*/rtl/foo.sv` does not; and **no comment
anywhere may begin with the word that names the tool** — it parses those as directives.

That last one is not confined to `.vlt` files, which is how the MC68010 project met it.
A perfectly ordinary comment in `rtl/rd68021_sync.sv`, explaining why a parameter is
sized by `WIDTH`, wrapped so that a line began with the tool's name followed by the
warning it was talking about:

```
    // by WIDTH rather than fixed at 32 bits, or every instantiation is a
    // <toolname> WIDTHEXPAND.
```

```
%Error: rtl/rd68021_sync.sv:21:5: Unknown verilator comment: '/*verilator WIDTHEXPAND.*/'
```

The rule in practice: **do not name that tool at the start of a comment line, in any
file.** Name the warning, or reword. The other five front-ends have nothing to say about
it, so lint is where it is caught and nowhere else.

This has now been hit three times in two milestones, each time in an ordinary comment
that happened to wrap onto a line beginning with the word. `grep -rn '^\s*//\s*<name>'
rtl/ sim/` finds them all in one go and is worth running before a commit.

## The function-in-a-continuous-assignment trap *(measured)*

This one cost real debugging time on the MC68010 project and no lint run catches it.

```systemverilog
function automatic logic [31:0] src_mux(input logic [3:0] sel);
  case (sel) ... SRC_RDATA: src_mux = read_data; ... endcase   // reads module state
endfunction

assign a_bus = src_mux(f_asrc);      // WRONG
```

**iverilog re-evaluates the function only when its explicit arguments change.**
`read_data` is not an argument, so `a_bus` keeps a stale value when the read data arrives
— silently, with no warning. Verilator and yosys infer the real dependency and behave as
intended, so lint is clean under all three tools and only simulation shows the difference.

Write it as `always_comb` instead, whose sensitivity is inferred from everything the
statements read. The rule is not "convert functions that are currently wrong"; it is **do
not call a function that reads module state from a continuous assignment at all**, because
whether the bug is visible depends on which combinations the design happens to exercise.
A function whose result depends only on its arguments is still fine anywhere.

## Known tool quirks *(measured)*

| Tool | Quirk | Workaround |
|---|---|---|
| iverilog | `always_comb` that reads nothing warns "process has no sensitivities" | drive constants with `assign` |
| iverilog | declarations inside an unnamed `begin`/`end` are a syntax error | declare at module scope |
| iverilog | assigning a ternary of two enum values to an enum variable is "This assignment requires an explicit cast" | use `if`/`else` inside the case item |
| iverilog | `unique`/`unique0` on a case are parsed but ignored, with a "sorry" note per occurrence | harmless; keep them for the other five |
| iverilog | adjacent string literals do not concatenate (`"a" "b"` is a syntax error) | write one string |
| Verilator | `-Wall` flags every unused package parameter | a `.vlt` waiver with a reason |
| yosys | no `import` in any form | fully-scoped references |
| Vivado | needs the package file read before its users | the file list is dependency-ordered, packages first |
| Vivado | a signal used before its declaration is only `[Synth 8-6901]`, an *info* | `synth.tcl` and `impl.tcl` promote it to an error; Questa rejects the same thing natively |
| Vivado | the ROM mapping in its own synthesis report is **preliminary**, and timing optimisation may reverse it afterwards with no message — the report still said Block RAM, the netlist had none, and the microcode store came back as 1900 extra LUTs | say which memory and stop it being a choice: `(* rom_style = "block" *)` on the store's output register |
| Quartus | infers no ROM from a `case` whose index has fewer than half its values labelled -- the microcode store at 1,906 of 4,096 -- whatever attribute it carries, and builds it from logic with no message saying why; `rom_style` itself is not recognised (`Warning (10335)`) | index the `case` by the bits the contents need; `tools/ucode/assemble.py` does, and selects the default entry for the bits above with a registered flag. No Quartus attribute is needed then -- it picks the block for the family |
| Quartus, MAX 10 | block memory is initialised only in the configuration mode that carries it; in any other, "MIF is not supported for the selected family" and every ROM falls back to logic | `INTERNAL_FLASH_UPDATE_MODE "SINGLE IMAGE WITH ERAM"`; `scripts/quartus.tcl` sets it for the family |
| Quartus | `small` is a reserved word in its SystemVerilog | do not name a module or signal that |
| Quartus | a package-scoped constant inside a module instantiation's **port expression** is not resolved: it becomes an implicit one-bit net named after the constant, `Warning (10236)`, and the netlist quietly stops matching the source. `quartus_map` returns 0 either way | hoist the expression into a named signal and connect that; `make lint-quartus` greps for `Implicit Net warning` and fails on it, because the exit code would not |
| Quartus | an ordered `casez` — first match wins — is built as a priority chain and not flattened. 1401 patterns became 498 logic levels and 4.67 MHz where Vivado and yosys flatten the same source | emit disjoint patterns instead; `tools/ucode/assemble.py` resolves the order once, in Python, and proves the two tables equivalent over every input |
| iverilog | a package-scoped constant in a **port declaration** is a bare "syntax error" with no hint as to which token it minded | write the width as a literal. `tools/ucode/assemble.py` knows the numbers and emits them |
| iverilog | `return` in a task is "Cannot return from tasks" | a `while` loop with a flag |
| all of them | a plain `$(wildcard rtl/gen/*.sv)` sorts a generated module ahead of the generated package it depends on | split the wildcard so packages come first |
| Questa | a variable read above its own declaration is `(vlog-2730) Undefined variable`, then `(vlog-2388) already declared in this scope` at the declaration | declare before first use; iverilog and Verilator invent an implicit net instead |
| Quartus | `quartus_sh -t` reports a failed Tcl script as `Error (23031): Evaluation of Tcl script unsuccessful` and **throws the message away**, so a typo and a missing file look identical | wrap the script body in a proc and `catch` it, printing `$::errorInfo`; `scripts/quartus.tcl` does |
| Quartus | `project_new` and `project_open` **change the working directory** to the project's, so every relative path taken from the command line silently stops resolving after that line | record `[pwd]` first and `file join` it onto every path — the file list, the SDC file and each source |
| Quartus | `$quartus(args)` is a global array and is not visible inside a proc | `$::quartus(args)` |
| Vivado | `get_timing_paths` on a design with nothing between flops returns an empty list, and `get_property` on it is `[Common 17-55] 'get_property' expects at least one object` — an *error*, which fails an otherwise successful synthesis of a skeleton | ask for `-quiet` and test `llength` before reporting |
| all of them | a pipe into `tee` or `grep` throws the exit status away, so a gate that pipes its tool's output **cannot fail** | `set -o pipefail` on every recipe line that pipes. Measured here: a Tcl error inside `quartus_sh` reported `quartus: ok`. Better still, run the tool into a log and decide from its exit status — `tool \| grep -v … \|\| test $? -eq 1` hands the *tool's* status to `test`, which then succeeds |
| yosys | returns 0 on a warning, and two of its warnings are defects: "multiple conflicting drivers" (a register written from two processes — which is what a register written from both edge domains looks like) and an inferred latch | grep the log; the exit code will not tell you. Measured: `op_addr` was driven from both the posedge and the negedge block and `make lint` said PASS |
| Vivado | `[Synth 8-3332]` reads like an implicit-declaration message and is not — it is "sequential element … is unused and will be removed", ordinary optimisation, and the normal state of a design whose upper units are still stubs | do **not** promote it. Promoting it failed a perfectly good synthesis run. `[Synth 8-6901]`, used-before-declaration, is the one worth promoting, and it has already earned its keep |
| all of them | a testbench that samples an instruction boundary just after the rising edge misses the end of a bus-cycle microword, because the bus unit's output stage is negedge-clocked and the acknowledge only settles in the second half of the clock | sample instruction boundaries on the *falling* edge |
| all of them | a testbench that *releases reset* on a rising edge races the design, because every register takes its reset value on that same edge and both are non-blocking assignments at the same time step. The tell is that adding a `$display` to the harness changes the result | a testbench changes an input the design samples on a rising edge only on a **falling** edge. Measured here: the first vector of a sweep behaved differently from the same vector replayed later |
| Quartus | a macro expanding to a **package-scoped part-select** (`` `UF(EAPC) `` → `pkg::U_EAPC_LSB +: pkg::U_EAPC_W`) is fine in a procedural block and **not** in a port connection: Quartus reads both scoped names as undeclared identifiers, creates implicit nets and builds a netlist that does not match the source, exit code 0 | bind it to a named signal and pass that. Measured here on `u_eadec.pc_base`; iverilog, Verilator, yosys, Vivado and Questa all accepted it |
| yosys | a variable **declared inside an `always_ff`** and assigned with `=` reads like a temporary and is not one: yosys gives it storage, and the flip-flops it makes have no reset because nothing in the reset branch mentions them | declare it outside and drive it with a continuous assignment. Measured here on a four-bit register select inside MOVEM's destination arm: `make lint` passed and `make audit` reported four flip-flops initialising outside reset |
| Quartus, Vivado, Questa | a signal **used before it is declared** inside a module. iverilog, Verilator and yosys all accept it; Quartus creates an implicit net and builds a netlist that does not match the source, Vivado's `[Synth 8-6901]` says so, and Questa refuses with `Undefined variable` | declare every signal above its first use. Measured here on six signals the datapath's source multiplexers read from units written further down the file: three front-ends green, three refusing |

Two of these are checked by `tools/src_lint.py`, which is part of `make lint`:
a signal used above its declaration, and a package-scoped name (or the `` `UF ``
macro) in a port connection. Both were in this table and both came back anyway
during M10, found only when M11 ran Quartus and Questa -- twenty signals and one
port. The greps below are the rest, and are worth running before a vendor run
rather than after one:

```sh
# a package-scoped name inside a port connection -- Quartus makes an implicit
# net of it and says so only in a warning
grep -nE "\.[a-z_0-9]+ *\([^)]*::" rtl/*.sv

# a comment line that begins with the linter's own name, which it reads as a
# directive
grep -nE "^\s*// *Verilator" rtl/*.sv sim/**/*.sv
```

Add to this table whenever a tool surprises you. It is cheaper than rediscovering it.

## Two cautions about FPGA numbers *(measured)*

**Place and route varies more than small changes do.** On the MC68010 project, two runs
differing only in the contents of one unreachable microcode word came out 1.3 ns apart.
Treat anything under about 1.5 ns as a statement about the router.

**Do not convert a slack into a frequency by dividing.** Every critical path here launches
on one clock edge and captures on the next, so its budget is half the period and both
halves shrink together. The shortest period is `T − 2 × slack`, not `T − slack`.
Frequencies are measured by re-running place and route, never extrapolated.
