# DRAM Access Simulator

A trace-driven **DRAM timing simulator** in Python with an interactive **Streamlit dashboard**, inspired by the architecture of [DRAMSim2](https://ieeexplore.ieee.org/document/5713095) and [Ramulator](https://github.com/CMU-SAFARI/ramulator2).

It connects two levels of DRAM behavior:

- **Architecture level** — how a memory controller turns physical addresses into DRAM commands (`ACTIVATE`, `READ`, `WRITE`, `PRECHARGE`), enforces core timing constraints (`tRCD`, `tCL`, `tRP`, `tRAS`, `tRC`), classifies each access as a **row-buffer hit / miss / conflict**, and compares **FCFS** vs **FR-FCFS** scheduling.
- **Cell level (1T1C)** — an educational model of the DRAM cell itself: capacitor charge decay from leakage, **destructive reads** with sense-amplifier restore, periodic **refresh**, and what happens when refresh comes too late (retention failure).

![architecture](https://img.shields.io/badge/python-3.10%2B-blue) ![streamlit](https://img.shields.io/badge/UI-Streamlit-red)

## DRAM concepts modeled

### 1. Organization & address mapping
A DRAM system is organized as **Channel → Rank → Bank → Row → Column**. Each bank has a **row buffer** (sense amplifiers) that holds one open row at a time. This simulator slices a flat physical address into coordinates using the bit layout (LSB → MSB):

```
| row | rank | bank | channel | column |
```

Consecutive addresses stay in the same row (spatial locality → row hits), while different rows spread across banks (bank-level parallelism).

### 2. Commands and timing
To access data, the controller must:

| Command | Purpose | Constraint |
|---|---|---|
| `ACTIVATE` | Load a row into the bank's row buffer | `tRCD` cycles before READ/WRITE |
| `READ` / `WRITE` | Column access on the open row | data after `tCL` cycles |
| `PRECHARGE` | Close the open row (restore the bank) | `tRP` before the next ACTIVATE; not before `tRAS` after ACTIVATE |

`tRC` (≈ `tRAS + tRP`) limits how fast the same bank can be re-activated.

### 3. Row-buffer outcomes
| Outcome | Bank state | Commands | Latency |
|---|---|---|---|
| **Hit** | requested row already open | `READ` | `tCL` |
| **Miss** | no open row (bank precharged) | `ACT → READ` | `tRCD + tCL` |
| **Conflict** | a *different* row is open | `PRE → ACT → READ` | `tRP + tRCD + tCL` |

### 4. Scheduling policies
- **FCFS** — service requests strictly in arrival order.
- **FR-FCFS** (First-Ready FCFS) — among pending requests, prefer the oldest one that hits the open row; otherwise the oldest. This exploits row-buffer locality and typically reduces total cycles on bursty traces.

### 5. The 1T1C cell — why DRAM needs refresh
A DRAM bit is charge on a tiny capacitor behind one access transistor:

```
wordline ─────┐
              │ (gate)
bitline ─────[T]────●────||──── GND
                 storage    C
                  node   capacitor
```

The **"1T1C Cell Visualizer"** tab models (in normalized units):

| Phenomenon | Model |
|---|---|
| **Leakage** | stored charge decays as `q(t) = q₀ · e^(−t/τ)` (τ = retention time) |
| **Sensing** | sense amplifier reads 1 iff charge ≥ threshold |
| **Destructive read** | reading shares cell charge with the bitline (charge × (1 − share)), then a **restore** rewrites the sensed value at full strength |
| **Refresh** | periodic internal sense + restore; if it arrives *after* the charge crossed the threshold, the amplifier senses 0 and restores the **wrong** value → retention failure |
| **Refresh overhead** | fraction of time a rank is busy refreshing ≈ `tRFC / tREFI` (~4–5 % on DDR4) |

This is a deliberately simplified educational model — not a transistor-level SPICE simulation.

## Repository structure

```
dram-simulator/
├── app.py                  # Streamlit dashboard (UI only, two tabs)
├── requirements.txt
├── README.md
├── src/                    # Simulator core — no Streamlit dependency
│   ├── dram_config.py      # Organization + timing parameters
│   ├── address_mapper.py   # Address → Ch/Rank/Bank/Row/Col
│   ├── dram_bank.py        # Bank state machine + timing model
│   ├── scheduler.py        # FCFS / FR-FCFS policies
│   ├── memory_controller.py# Request queue → scheduler → banks
│   ├── simulator.py        # Trace loading, run, statistics
│   └── dram_cell.py        # Educational 1T1C cell model (leakage/refresh)
├── data/
│   └── sample_trace.csv    # Bundled example workload
└── tests/
    ├── test_simulator.py   # architecture-level unit + end-to-end tests
    └── test_dram_cell.py   # cell-level model tests
```

## Quick start

```bash
git clone <your-repo-url> && cd dram-simulator

python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt

# Run the tests
pytest

# Launch the dashboard
streamlit run app.py
```

Open http://localhost:8501, tweak the timing sliders, upload your own trace, and compare FCFS vs FR-FCFS.

## Trace format

CSV with a header, one request per line. Addresses can be hex (`0x...`) or decimal; `#` lines are comments.

```csv
cycle,address,op
0,0x0000,READ
0,0x4000,READ
30,0xA400,WRITE
```

`cycle` is the request's arrival time at the memory controller.

## Deploying on Streamlit Community Cloud

1. Push this repository to GitHub (public repo).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub.
3. **New app** → pick your repo/branch → set **Main file path** to `app.py` → **Deploy**.

`requirements.txt` is detected automatically; no other configuration is needed.

## Model simplifications (MVP scope)

This is a controller-level *timing* simulator, not a cycle-accurate device model:

- No `REFRESH`, `tCCD`, `tBURST`, or data-bus contention in the *architecture-level* command stream (refresh physics and its `tRFC/tREFI` overhead are modeled in the separate 1T1C cell module).
- `WRITE` latency approximated with `tCL` (no separate write-recovery `tWR`).
- The controller issues one request per cycle; per-bank timing constraints provide the serialization within a bank, so accesses to different banks overlap.
- Open-page row-buffer policy only.

These are deliberate: the goal is a **correct, readable model of the concepts** (address mapping, bank state, timing constraints, scheduling) that is easy to extend — e.g., adding a refresh engine or per-standard timing tables Ramulator-style.

## References

- P. Rosenfeld, E. Cooper-Balis, B. Jacob, *DRAMSim2: A Cycle Accurate Memory System Simulator*, IEEE CAL 2011.
- Y. Kim, W. Yang, O. Mutlu, *Ramulator: A Fast and Extensible DRAM Simulator*, IEEE CAL 2015.
- S. Rixner et al., *Memory Access Scheduling*, ISCA 2000 (FR-FCFS).
