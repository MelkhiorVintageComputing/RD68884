# Boards

## The IIsiA7 Mini: an MC68881 for the Macintosh IIsi

`make board-iisia7` builds RD68884 for the IIsiA7 Mini. That is a card for the Processor Direct Slot (PDS) of a Macintosh IIsi, whose MC68030 has no FPU of its own. The card answers the MC68030's coprocessor cycles as an MC68881 would.

**Outputs**, in `build/iisia7_mini/`:
- `rd68884_iisia7.bit`, the bitstream;
- `rd68884_iisia7.bin`, the SPI flash image, as the board's own flow writes it;
- `rd68884_iisia7.mcs`, the same image in Intel hex;
- the reports.

**In the machine:**
- the flash image, written over JTAG and booted from flash (DONE high, the build's timestamp in USR_ACCESS), lets the Macintosh IIsi boot;
- utilities identify the FPU as an MC68881, as it presents itself;
- a quick benchmark puts it roughly on par with a 20 MHz MC68882, the usual FPU for this machine.

### The board

**Sources:** `Inputs/IIsiFPGA` (a git submodule; read-only, like everything under `Inputs/`):

| File | Contents |
|---|---|
| `IIsi-to-ztex-gateware/IIsiA7_Mini_pds.py` | The LiteX platform: pins, IO standards, configuration |
| `IIsiA7_Mini/` | The KiCad design, and its netlist `IIsiA7_Mini.xml` |

**The FPGA** is an xc7a50tftg256-1, the variant built for (`IISI_PART`).

**The bus.**
- The MC68030's bus reaches the FPGA through 74CB3T16211 bus switches. Their output enables are tied to ground, so they are always on.
- The FPGA's own three-state buffers therefore drive D, DSACK and HALT. There is no direction pin.
- The PDS pins are LVTTL. The order of the platform's pin lists was checked against the netlist through the switches: A0 is M1, FC0 is G14, DSACK0 is E13, and so on.

**The motherboard's FPU select** (`~FPU`, PDS A2) is not wired to the FPGA. The chip select is decoded from the address and FC.

**HALT has a buffer of its own** (74LVC1G125, U7). It holds HALT low until the FPGA's DONE rises, so the MC68030 is halted while the FPGA configures.

### The design (`boards/iisia7_mini/rd68884_iisia7_top.sv`)

It is Xilinx-specific (MMCM, BUFG), so it sits outside `rtl/` and its six-tool rule. Its registers still take their values from reset branches; `make audit` checks its source.

| | |
|---|---|
| Core clock | The 100 MHz oscillator through an MMCM: ×10 to a 1000 MHz VCO, then ÷ `BOARD_DIVIDE`. 20 gives 50 MHz |
| BIU | The asynchronous one (`BUS_SYNC = 0`), as everywhere but the same-clock builds. The MC68030's clock is not used |
| Reset | RD68884's `rst_n` is the MMCM's LOCKED, synchronised. The PDS RESET pin is the architectural reset (FPU 9.9) |
| Chip select | Early, without AS: FC = 7, A19–A16 = 2, A15–A13 = 1. Coprocessor ID 1, the FPU's (FPU 10.3) |
| Port | 32 bits: SIZE and A0 strapped high (FPU table 9-2) |
| HALT | Held low from configuration until RD68884 is out of reset, then three-stated for good. The bitstream enables the I/O (startup cycle 5) before DONE (6), so HALT passes from U7 to the FPGA with no gap. The MC68030 never asks an FPU that cannot answer |
| DSACK | Actively negated before release (`DSACK_NEGATE = 1`), as the MC68881 does (FPU 9.8; `doc/bus-timing.md`) |
| LEDs | D4: FPU ready. D6: a coprocessor-interface access, stretched to 84 ms. E6: RESET asserted. K5: HALT held |
| Everything else | Not in the design, three-stated with no pull (`BITSTREAM.CONFIG.UNUSEDPIN PULLNONE`). The DDR2, HDMI, serial port, the second oscillator, and every PDS signal the FPU has no business with |

### Pins and constraints

- **`tools/board_pins.py`** writes the pin constraints from the platform file itself. It loads the file with stand-ins for LiteX, which is not installed. A port of the top that the platform does not declare is an error.
- **`boards/iisia7_mini/timing.xdc`:**
  - No path from a bus pin into a register is timed: the bus is asynchronous and the BIU is built for it.
  - What is timed is the BIU's combinational response at the pins: START falling releases the data and negates DSACK. Pin to pin, that is bounded at 25 ns against the MC68881's 30/30/40 ns at 20 MHz (specifications 16, 21, 22). Register to pin is bounded at 15 ns.
- **The bitstream properties** are the platform's: SPI ×4 at 50 MHz, compressed, `CFGBVS VCCO`, 3.3 V. Plus the two above, unused pins and DONE's startup cycle.
- **The flash image** is written as the platform's `write_cfgmem` writes it: 16 MB, SPI ×4, the bitstream at 0. The platform's flow also loads the HDMI gateware's declaration ROM at `0x280000`; an FPU has none.

### The build

```sh
git submodule update --init           # Inputs/IIsiFPGA
make board-iisia7                     # 50 MHz
make board-iisia7 BOARD_DIVIDE=25     # 40 MHz, if 50 ever stops meeting timing
```

| | |
|---|---|
| Core clock | **50 MHz, met.** Worst setup slack +2.09 ns on the core's own critical path, the microword to `A`'s exponent, as out of context (`doc/size-and-speed.md`) |
| Bus pins | Pin to pin +6.72 ns, register to pin +4.51 ns |
| Utilisation | 4447 LUTs (14% of the 50T), 949 flip-flops, 6 DSPs, 21 block RAMs, 79 I/O |
| DRC | No errors; BRAM and DSP advisories only |

**RD68885, the MC68882 build** (doc/rd68885.md):

```sh
make board-iisia7 MODEL=68882 BOARD_DIVIDE=14    # 71.43 MHz; writes rd68885_iisia7.*
```

| | |
|---|---|
| Core clock | **71.43 MHz, met.** Worst setup slack +0.071 ns, on the same path as RD68884's (the microword to `A`'s exponent), not the conversion unit's. RD68884 has +0.387 ns at the same clock |
| Utilisation | 1419 slices (17.4% of the 50T; RD68884 1181, 14.5%), 5101 slice LUTs, 1314 flip-flops, 6 DSPs, 22 block RAMs, 79 I/O |
| Build directory | `build/iisia7_mini-68882/` |
| In the IIsi | Works as the machine's FPU |

### Checked in simulation

`make sys BOARD=iisia7` runs the board top itself in `sys_tb`, on RD68021:
- its 100 MHz oscillator and the MMCM, through a stand-in (`sim/models/mmcme2_base.sv`);
- its chip select from the full address and FC;
- DSACK and HALT as wires with the motherboard's pull-ups, HALT going to the CPU.

Every program passes on the 32-bit port, in lockstep with the ISS. Each run must also see HALT held, then released.
