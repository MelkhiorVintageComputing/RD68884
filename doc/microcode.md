# The microcode machine

The sequencer, its datapath and the microcode: `rtl/rd68884_seq.sv`, `rtl/rd68884_regfile.sv` and `tools/ucode/`.

The four parts and where each is defined:

| Part | Defined in |
|---|---|
| Field table | `tools/ucode/fields.py`. The assembler, the generated RTL package and the ISS all read it |
| Executable meaning of every field | `tools/iss/core.py` |
| RTL transcription | `rtl/rd68884_seq.sv` |
| The program | `tools/ucode/program.py` |

## How the pieces are checked

| Check | What it compares |
|---|---|
| `make iss-test` | The microcode on the ISS, with a Python model of the BIU's registers, against the golden model `tools/model/cpif.py`. Both are driven through the CIRs by the same MC68020 driver. The registers, main-processor state, memory (so FSAVE frames), exceptions and primitives must all match |
| `make sys` | `sim/programs/fpu_m4.S` on RD68021 with RD68884 as its coprocessor, for 32-, 16- and 8-bit FPU ports |
| `tools/iss/lockstep.py` (part of `make sys`) | The RTL sequencer against the ISS, every clock of the 32-bit run: the recorded BIU signals are replayed into the ISS, and the micro-address and every enabled output must be identical |

## Timing rules

- **One microinstruction per clock.** The store is a block-RAM `case` ROM read at the *next* micro-address, so the word arrives with its address.
- **Every action reads the state as it was at the start of the clock.** Results land at the end. For example, `RF WRITE` stores the old `A` even when the same word loads a new one.
- **The register file reads synchronously.** `RF READ` fills `RFQ` at the end of the clock; `ASRC=RFQ` in a later word uses it.
- **The transfer bus.** `TBUS` is chosen by `TSRC`. `T`, the `TDST` destination and a BIU write (`OPR_WR`, `SAVE_WR` and the rest) all take it in the same clock.

## Traps

A trap overrides the next micro-address and kills the current word's actions:

| Trap | Cause | Entry |
|---|---|---|
| Reset | The RESET pin, while it is asserted | `reset` |
| Abort | A control CIR write (FPU 7.2.2) | `abort` |
| Restore | A rising restore request, i.e. a restore CIR write (FPU 7.2.4) | `restore` |

## Events

The BIU raises one-clock events: a response read, a register-select read, a save word read. The core keeps a sticky copy of each, so the microcode never depends on reacting within a clock. The action that makes the next event meaningful re-arms it:

- a response write re-arms `RESP_READ`;
- `RSEL_WR` re-arms `RSEL_READ`;
- `SAVE_WR` re-arms `SAVE_READ`.

## The races the BIU settles in hardware

Each of these was found by the differential tests. Each needs the BIU, because no microcode reaction time is fast enough.

| Race | Fix |
|---|---|
| A command arrives while the microcode is finishing the previous instruction, and the microcode's final `$0802` overwrites the BIU's `$8900` | `RESP=WRC` and `CLEAR` leave the response alone if a command is latched |
| The MPU reads the register select CIR and writes or reads the registers at once | The BIU switches its expectation itself, from `rsel_dir` |
| FSAVE/FRESTORE frames are not followed by a response read | The BIU counts the frame's long words (`XFER`) and expects a command after the last |
| After FRESTORE of a frame with a pending instruction, the interrupted MPU re-reads the response before the microcode has rebuilt the first primitive | The restore CIR write itself sets the response to `$8900`. The restart path skips `CLEAR` |

## The value format

`A`, `RFQ` and the register file hold three fields:

| Field | Width | Contents |
|---|---|---|
| Sign | 1 | |
| Exponent | 18 | Two's complement, unbiased: the biased exponent minus 16383. 16384 marks infinity or NaN |
| Mantissa | 72 | The explicit integer bit at 71, the extended fraction at 70–8, eight extra bits below |

`UNPACKX` and `PACKX` convert to and from the extended memory format (FPU table 3-3). The exponent is copied with the bias removed or added back, and nothing is normalised, so FMOVEM and FMOVE.X move unnormals, denormals and NaN payloads bit for bit. The condition codes are derived from this format (FPU table 2-1).

## M4 scope

Implemented:
- every coprocessor dialog;
- FMOVE.X in, out and between registers;
- the control registers;
- FMOVEM with static and dynamic lists;
- the conditionals, BSUN included;
- pre-instruction exceptions;
- F-line;
- protocol violations and aborts (in the BIU);
- FSAVE/FRESTORE of null and idle frames.

Any other general instruction answers F-line until M5. A save request while operands are part-way through a transfer is not yet serviced: the busy frame is M8.
