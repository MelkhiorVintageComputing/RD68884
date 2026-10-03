# Where RD68884 deliberately differs from the MC68881

These differences are visible at the pins or to software. Each is a choice, made for size or simplicity, that the protocol permits. Choices made where the manual is silent are in `doc/model.md`; readings of contradictions are in `doc/manual-contradictions.md`.

| Behaviour | MC68881 | RD68884 | Why it is safe |
|---|---|---|---|
| Response and save CIR reads | Synchronous read cycles, 1.5 FPU clocks after START (FPU 10.4.1) | Asynchronous, like every other CIR | The main processor waits for DSACK whatever its timing |
| DSACK latency | 3-clock cycles when not busy (specification 19) | A few core clocks of synchronisation and decode: wait states | As above; doc/bus-timing.md |
| DSACK release | Actively driven high, then three-stated (FPU 9.8) | Three-stated at once; the board's pull-ups raise the lines | DSACK negation and release (specifications 21/22) are met immediately; the pull-ups the manual already requires do the rest |
| Busy state frame | Saved in the initial phase, and in the middle phase at microcode checkpoints (FPU 6.4.3) | Saved only when operands are part-way through transferring. While a computation runs, FSAVE answers come-again until it finishes | Come-again is legal in every phase; interrupt latency can grow by the length of the longest instruction |
| Busy state frame contents | Opaque | Opaque, our own layout (doc/model.md) | FPU 6.4.2.3: the frame must not be modified or interpreted |
| A protocol-violating operand read | "Inconsistent data" | All ones | The access is a violation either way |

## Found in RD68021 by M8 (not a divergence of RD68884)

**A bus-error handler that uses the coprocessor.** Take a bus error on an operand that a coprocessor primitive is moving, for example part-way through FMOVEM. If the handler executes coprocessor instructions besides FSAVE and FRESTORE before its RTE, RD68021 resumes the transfer wrongly: its next operand write to the FPU is word-sized (`$7FFE7FFE` for `$7FFE0000`), and a protocol violation follows. A real MC68020 keeps that state in its long bus-fault frame, so a page-fault handler may switch FPU contexts.

**The repro:** `sim/programs/fpu_m8.S`, with `h_berr` replaced by a `bsr switch`.

**What RD68884 does:** with FSAVE and FRESTORE alone around the RTE, which is what `fpu_m8.S` does, the busy frame resumes correctly on all three port widths. ../RD68021 is read-only from here: this is recorded for its own repository.
