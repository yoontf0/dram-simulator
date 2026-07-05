"""Streamlit dashboard for the DRAM simulator.

Two levels, two tabs:
  Tab 1 — architecture-level trace simulation (banks, timing, scheduling)
  Tab 2 — 1T1C cell-level visualization (charge decay, refresh, destructive read)

UI layer only — all simulation logic lives in src/ and is fully usable
without Streamlit (see tests/).
"""

import io
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.dram_cell import CellEventType, CellParams, refresh_overhead, simulate_cell
from src.dram_config import DRAMConfig
from src.simulator import SimulationResult, load_trace, run_simulation

SAMPLE_TRACE = Path(__file__).parent / "data" / "sample_trace.csv"
OUTCOME_COLORS = alt.Scale(domain=["hit", "miss", "conflict"],
                           range=["#2ecc71", "#f1c40f", "#e74c3c"])

st.set_page_config(page_title="DRAM Simulator", page_icon="🧠", layout="wide")
st.title("🧠 DRAM Simulator")
st.caption(
    "Trace-driven DRAM timing simulator inspired by DRAMSim2 / Ramulator, plus an "
    "educational 1T1C cell-level model showing why DRAM needs refresh."
)

tab_arch, tab_cell = st.tabs(["🚌 DRAM Access Simulator", "🔋 1T1C Cell Visualizer"])

# =====================================================================
# Tab 1 — Architecture-level access simulator
# =====================================================================

with st.sidebar:
    st.header("DRAM Configuration")
    st.caption("Applies to the *DRAM Access Simulator* tab.")

    preset = st.selectbox("Preset", ["DDR4-2400-like", "Custom"])
    if preset == "DDR4-2400-like":
        trcd, tcl, trp, tras = 14, 14, 14, 33
    else:
        trcd = st.slider("tRCD (ACT→RD/WR)", 1, 40, 14)
        tcl = st.slider("tCL (RD/WR→data)", 1, 40, 14)
        trp = st.slider("tRP (PRE→ACT)", 1, 40, 14)
        tras = st.slider("tRAS (ACT→PRE min)", 1, 80, 33)
    st.caption(f"tRC = tRAS + tRP = **{tras + trp}** cycles")

    num_banks = st.select_slider("Banks per rank", options=[2, 4, 8, 16], value=8)

    st.header("Scheduling")
    policy = st.radio("Policy", ["FCFS", "FR-FCFS"], index=1, horizontal=True)
    compare = st.checkbox("Compare both policies", value=True)

config = DRAMConfig(num_banks=num_banks, tRCD=trcd, tCL=tcl, tRP=trp, tRAS=tras, tRC=tras + trp)


def results_frame(sim: SimulationResult) -> pd.DataFrame:
    """Per-request detail table for charts/tables."""
    rows = []
    for r in sim.results:
        loc = r.request.location
        rows.append(
            {
                "req_id": r.request.req_id,
                "arrival": r.request.arrival_cycle,
                "address": f"0x{r.request.address:06X}",
                "op": "WRITE" if r.request.is_write else "READ",
                "bank": loc.bank,
                "row": loc.row,
                "col": loc.column,
                "result": r.access_type.value,
                "start": r.start_cycle,
                "done": r.done_cycle,
                "latency": r.latency,
            }
        )
    return pd.DataFrame(rows)


def commands_frame(sim: SimulationResult) -> pd.DataFrame:
    """Flattened command stream for the timeline chart."""
    rows = [
        {"cycle": c.cycle, "command": c.command.value, "bank": f"Bank {c.bank_key[2]}", "row": c.row}
        for r in sim.results
        for c in r.commands
    ]
    return pd.DataFrame(rows)


with tab_arch:
    st.subheader("1. Input trace")
    uploaded = st.file_uploader("Upload a trace CSV (`cycle,address,op`)", type=["csv"])
    if uploaded is not None:
        trace = load_trace(io.TextIOWrapper(uploaded, encoding="utf-8"))
        st.success(f"Loaded {len(trace)} requests from `{uploaded.name}`.")
    else:
        trace = load_trace(SAMPLE_TRACE)
        st.info(f"Using bundled sample trace ({len(trace)} requests). Upload a CSV to replace it.")

    policies = ["FCFS", "FR-FCFS"] if compare else [policy]
    sims = {p: run_simulation(config, trace, p) for p in policies}
    main = sims[policy] if policy in sims else sims[policies[0]]

    st.subheader("2. Summary")
    if compare:
        cols = st.columns(len(policies))
        for col, p in zip(cols, policies):
            s = sims[p].stats
            with col:
                st.markdown(f"**{p}**")
                m1, m2 = st.columns(2)
                m1.metric("Total cycles", s.total_cycles)
                m2.metric("Avg latency", f"{s.avg_latency:.1f}")
                m3, m4 = st.columns(2)
                m3.metric("Row-hit rate", f"{s.hit_rate:.0%}")
                m4.metric("Hit / Miss / Conf", f"{s.hits} / {s.misses} / {s.conflicts}")
    else:
        s = main.stats
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total cycles", s.total_cycles)
        m2.metric("Avg latency (cycles)", f"{s.avg_latency:.1f}")
        m3.metric("Row-hit rate", f"{s.hit_rate:.0%}")
        m4.metric("Hit / Miss / Conflict", f"{s.hits} / {s.misses} / {s.conflicts}")

    df = results_frame(main)
    st.subheader(f"3. Visualization — {main.stats.policy}")

    left, right = st.columns(2)
    with left:
        st.markdown("**Row-buffer outcome per request**")
        outcome = (
            df["result"].value_counts().reindex(["hit", "miss", "conflict"]).fillna(0).reset_index()
        )
        outcome.columns = ["result", "count"]
        st.altair_chart(
            alt.Chart(outcome)
            .mark_bar()
            .encode(
                x=alt.X("result", sort=["hit", "miss", "conflict"], title="Outcome"),
                y=alt.Y("count", title="Requests"),
                color=alt.Color("result", scale=OUTCOME_COLORS, legend=None),
            ),
            use_container_width=True,
        )
    with right:
        st.markdown("**Bank utilization (accesses per bank)**")
        bank_df = df.groupby("bank").size().reset_index(name="accesses")
        st.altair_chart(
            alt.Chart(bank_df).mark_bar().encode(
                x=alt.X("bank:O", title="Bank"), y=alt.Y("accesses", title="Accesses")
            ),
            use_container_width=True,
        )

    st.markdown("**Latency per request** (colored by row-buffer outcome)")
    st.altair_chart(
        alt.Chart(df)
        .mark_circle(size=90)
        .encode(
            x=alt.X("req_id", title="Request ID (trace order)"),
            y=alt.Y("latency", title="Latency (cycles)"),
            color=alt.Color("result", scale=OUTCOME_COLORS),
            tooltip=["req_id", "address", "op", "bank", "row", "result", "latency"],
        ),
        use_container_width=True,
    )

    st.markdown("**Command timeline** (each dot = one DRAM command on the command bus)")
    cmd_df = commands_frame(main)
    st.altair_chart(
        alt.Chart(cmd_df)
        .mark_point(size=80, filled=True)
        .encode(
            x=alt.X("cycle", title="Cycle"),
            y=alt.Y("bank", title="Bank"),
            color=alt.Color("command", title="Command"),
            shape="command",
            tooltip=["cycle", "command", "bank", "row"],
        ),
        use_container_width=True,
    )

    with st.expander("Per-request detail table"):
        st.dataframe(df, use_container_width=True, hide_index=True)

    if compare:
        other = "FCFS" if main.stats.policy == "FR-FCFS" else "FR-FCFS"
        a, b = sims[main.stats.policy].stats, sims[other].stats
        st.info(
            f"**{a.policy}** finished in **{a.total_cycles}** cycles with **{a.hit_rate:.0%}** "
            f"row-hit rate vs **{b.policy}**: {b.total_cycles} cycles, {b.hit_rate:.0%} hit rate. "
            "FR-FCFS reorders pending requests to exploit the open row buffer."
        )

# =====================================================================
# Tab 2 — 1T1C cell-level visualizer
# =====================================================================

with tab_cell:
    st.subheader("One Transistor + One Capacitor")
    intro_left, intro_right = st.columns([1, 2])
    with intro_left:
        st.code(
            "wordline ─────┐\n"
            "              │ (gate)\n"
            "bitline ─────[T]────●────||──── GND\n"
            "                 storage    C\n"
            "                  node   capacitor",
            language=None,
        )
    with intro_right:
        st.markdown(
            "A DRAM bit is stored as **charge on a tiny capacitor** behind one access "
            "transistor. The capacitor **leaks**, so a stored 1 fades toward 0 — this is "
            "why DRAM is *dynamic* and must be **refreshed**. Reading is **destructive**: "
            "the cell shares its charge with the bitline, the **sense amplifier** decides "
            "0 or 1, and a **restore** rewrites the value at full strength."
        )

    st.markdown("#### Simulation controls")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        tau = st.slider("Retention time τ (time units)", 5.0, 200.0, 40.0, step=5.0,
                        help="Leakage time constant: q(t) = q₀·e^(−t/τ)")
    with c2:
        use_refresh = st.checkbox("Enable refresh", value=True)
        refresh_iv = st.slider("Refresh interval", 5.0, 150.0, 25.0, step=5.0,
                               disabled=not use_refresh)
    with c3:
        reads_text = st.text_input("Read times (comma-separated)", "18, 55")
    with c4:
        share = st.slider("Read charge sharing loss", 0.0, 0.6, 0.35, step=0.05,
                          help="Fraction of cell charge lost to the bitline per read")
        threshold = st.slider("Sense threshold", 0.2, 0.8, 0.5, step=0.05)

    t_end = 100.0
    try:
        read_times = [float(x) for x in reads_text.replace(" ", "").split(",") if x]
    except ValueError:
        st.error("Read times must be numbers, e.g. `18, 55`")
        read_times = []

    params = CellParams(retention_time=tau, sense_threshold=threshold, read_charge_share=share)
    with_refresh = simulate_cell(
        params, write_value=1, t_end=t_end, read_times=read_times,
        refresh_interval=refresh_iv if use_refresh else None,
    )
    no_refresh = simulate_cell(params, write_value=1, t_end=t_end, read_times=read_times,
                               refresh_interval=None)

    # ---- Metrics ----
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Refresh operations", with_refresh.refresh_count)
    m2.metric("Retention failures", with_refresh.failure_count,
              delta=None if with_refresh.failure_count == 0 else "data lost!",
              delta_color="inverse")
    m3.metric("Final sensed value", with_refresh.final_value)
    m4.metric("Final value w/o refresh", no_refresh.final_value)

    if with_refresh.failure_count:
        st.error(
            "⚠️ The charge dropped below the sense threshold **before** the next "
            "refresh/read — the sense amplifier read a 0 and restored the wrong value. "
            "Shorten the refresh interval or increase τ."
        )
    elif use_refresh and no_refresh.final_value != 1:
        st.success(
            "✅ Refresh keeps the stored 1 alive. Without refresh the same cell decays "
            "below the threshold and the bit is lost (dashed line)."
        )

    # ---- Charge vs time chart ----
    scenario_frames = [
        pd.DataFrame(with_refresh.samples, columns=["time", "charge"]).assign(
            scenario="with refresh" if use_refresh else "no refresh (selected)")
    ]
    if use_refresh:
        scenario_frames.append(
            pd.DataFrame(no_refresh.samples, columns=["time", "charge"]).assign(
                scenario="no refresh")
        )
    sample_df = pd.concat(scenario_frames, ignore_index=True)

    ev_rows = [
        {
            "time": e.time,
            "kind": e.kind.value,
            "charge": e.charge_after,
            "sensed": e.sensed_value,
            "note": e.note,
        }
        for e in with_refresh.events
        if e.kind is not CellEventType.WRITE
    ]
    event_df = pd.DataFrame(ev_rows)

    line = (
        alt.Chart(sample_df)
        .mark_line()
        .encode(
            x=alt.X("time", title="Time (units)"),
            y=alt.Y("charge", title="Capacitor charge (normalized)",
                    scale=alt.Scale(domain=[0, 1.05])),
            color=alt.Color("scenario", title=None,
                            scale=alt.Scale(range=["#3498db", "#95a5a6"])),
            strokeDash=alt.StrokeDash("scenario", legend=None),
        )
    )
    threshold_rule = (
        alt.Chart(pd.DataFrame({"y": [threshold], "label": ["sense threshold"]}))
        .mark_rule(strokeDash=[6, 4], color="#7f8c8d")
        .encode(y="y", tooltip=["label", "y"])
    )
    layers = [line, threshold_rule]
    if not event_df.empty:
        event_points = (
            alt.Chart(event_df)
            .mark_point(size=140, filled=True)
            .encode(
                x="time",
                y="charge",
                color=alt.Color(
                    "kind",
                    title="Event",
                    scale=alt.Scale(
                        domain=["READ/SENSE", "RESTORE", "REFRESH", "RETENTION FAILURE"],
                        range=["#e67e22", "#2ecc71", "#3498db", "#e74c3c"],
                    ),
                ),
                shape=alt.Shape("kind", legend=None),
                tooltip=["time", "kind", "charge", "sensed", "note"],
            )
        )
        refresh_rules = (
            alt.Chart(event_df[event_df["kind"] == "REFRESH"])
            .mark_rule(color="#3498db", opacity=0.35)
            .encode(x="time")
        )
        layers += [refresh_rules, event_points]

    st.altair_chart(
        alt.layer(*layers).resolve_scale(color="independent").properties(height=380),
        use_container_width=True,
    )
    st.caption(
        "Solid line: cell charge with the selected settings. Dashed grey line: the same "
        "cell with refresh disabled. Vertical blue rules mark refresh operations; "
        "orange = destructive read (charge sharing), green = sense-amp restore, "
        "red = retention failure (wrong value sensed and restored)."
    )

    # ---- Refresh overhead ----
    st.markdown("#### Refresh overhead at the system level")
    o1, o2, o3 = st.columns([1, 1, 2])
    with o1:
        t_refi = st.number_input("tREFI (cycles between refreshes)", 100, 50000, 9360, step=100)
    with o2:
        t_rfc = st.number_input("tRFC (cycles per refresh)", 10, 5000, 420, step=10)
    overhead = refresh_overhead(int(t_rfc), int(t_refi))
    with o3:
        st.metric("Rank busy refreshing", f"{overhead:.1%}",
                  help="tRFC / tREFI — time the rank cannot serve requests")
        st.progress(min(overhead, 1.0))

    with st.expander("Why does DRAM require refresh? (explanation)"):
        st.markdown(
            """
**1. The capacitor leaks.** A DRAM cell stores a bit as charge on a capacitor of only
a few femtofarads. Junction and gate leakage drain it, so a stored **1** decays
exponentially — modeled here as `q(t) = q₀ · e^(−t/τ)`. Once the charge falls below
what the sense amplifier can distinguish from 0, the bit is **silently lost**.

**2. Refresh = read + restore, in time.** Before the charge crosses the sense
threshold, the DRAM internally *senses* each row and *rewrites* it at full strength.
JEDEC requires every cell to be refreshed within its retention window
(e.g. 64 ms — one refresh command per row every **tREFI ≈ 7.8 µs**).

**3. Reads are destructive.** Opening the access transistor shares the cell's charge
with the bitline — the stored level is degraded by the very act of reading. The sense
amplifier amplifies the tiny bitline swing to a clean 0/1, and that value is restored
into the cell (this is also why `tRAS` exists: the row must stay open long enough for
the restore to complete).

**4. Refresh is not free.** While a rank executes a refresh (tRFC), it cannot serve
memory requests — a bandwidth/latency tax of roughly `tRFC / tREFI` (~4–5 % on DDR4,
and growing with density since more rows must be refreshed per command).

**Try it:** set the refresh interval *longer* than `τ·ln(1/threshold)` ≈ the moment the
charge crosses the threshold — the next refresh will sense a 0, faithfully restore the
wrong value, and the red *retention failure* marker appears. That is exactly the data
loss real DRAM prevents by refreshing on time.
            """
        )
