# RD68884 ports

The ports of `rd68884_top` are the MC68881's pins (FPU section 9, table 9-5), with these conventions:
- **Three-state pins are split into `_i` / `_o` / `_oe`.** `_oe` high means this chip drives the pin. A board wrapper makes the real pad: `pad = oe ? o : 'z`.
- **`_n`** means active low at the pin.
- **A port with no `_oe`** is either always driven or an input.

| Port | Dir | Pin | Notes |
|---|---|---|---|
| `clk` | in | — | Core clock. **Not** the MC68881's CLK pin, except with the same-clock BIU; see below |
| `rst_n` | in | — | Hardware initialisation; every register resets from it |
| `reset_n_i` | in | RESET | Architectural reset (FPU 9.9) |
| `cs_n_i` | in | CS | Decoded externally: FC = 7, A19–A16 = 0010, A15–A13 = CpID (FPU 7.1) |
| `as_n_i` | in | AS | |
| `ds_n_i` | in | DS | |
| `rw_i` | in | R/W | 1 = read |
| `size_n_i` | in | SIZE | Strapped with `a_i[0]` for the port width (table 9-2) |
| `a_i[4:0]` | in | A4–A0 | CIR select; `a_i[0]` is a byte address on an 8-bit port |
| `d_i[31:0]` | in | D31–D0 | |
| `d_o[31:0]` | out | D31–D0 | |
| `d_oe[3:0]` | out | D31–D0 | One enable per byte lane, bit 3 = D31–D24 (see below) |
| `dsack_n_o[1:0]` | out | DSACK1, DSACK0 | Bit 1 = DSACK1 |
| `dsack_oe` | out | DSACK1, DSACK0 | Both lines are driven together |

**Port width straps (table 9-2)**

| Port | SIZE | A0 |
|---|---|---|
| 32-bit | high | high |
| 16-bit | high | low |
| 8-bit | low | the main processor's A0 |

**Data lanes.**
- On 16-bit and 8-bit ports the board ties the data lanes together (FPU 11.1, figure 10-2).
- The chip must therefore drive only the lane it is using, which is why there is one enable per byte lane rather than one for the bus.
- On a 32-bit port, a 16-bit CIR is always on D31–D16, acknowledged as a 16-bit port (table 9-3).

**DSACK** (FPU 9.8):
- The MC68881 actively drives the lines high after AS or DS rises, then floats them. RD68884 floats them at once (doc/divergences.md).
- The board needs pull-ups, as for the original.
- `dsack_oe` is high only while an access is being acknowledged.

**SENSE** (FPU 9.11) is a wire to ground on the die. It belongs to the board, not the core.

**The clock.**
- The MC68881's bus is asynchronous (FPU 10.4), so the core clock needs no relation to the main processor's.
- Any core clock works. A slower one only adds wait states to CIR accesses (doc/bus-timing.md). The datapath is constrained at 50 MHz, from a board PLL.
- `doc/architecture.md`, "Clocking", explains why.
- The MC68881's CLK pin is the PLL's reference and nothing else.

**The same-clock BIU** (`BUS_SYNC = 1`, doc/bus-timing.md) changes that:
- `clk` must be the main processor's CLK: the same clock, edge for edge, not just the same frequency.
- The core then runs at the bus clock, and CIR accesses take no wait states (one with `BUS_SYNC_WAIT = 1`).
- The other ports are unchanged.
