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
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from src.dram_cell import CellEventType, CellParams, refresh_overhead, simulate_cell
from src.dram_config import DRAMConfig
from src.simulator import SimulationResult, load_trace, run_simulation

SAMPLE_TRACE = Path(__file__).parent / "data" / "sample_trace.csv"
IMAGE_DIR = Path(__file__).parent / "docs" / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

OUTCOME_COLORS = alt.Scale(domain=["hit", "miss", "conflict"],
                           range=["#2ecc71", "#f1c40f", "#e74c3c"])

st.set_page_config(page_title="DRAM 시뮬레이터", page_icon="🧠", layout="wide")
st.title("🧠 DRAM 시뮬레이터")
st.caption(
    "DRAMSim2 / Ramulator에서 영감을 받은 trace 기반 DRAM 타이밍 시뮬레이터와, "
    "DRAM이 왜 refresh를 필요로 하는지 보여주는 교육용 1T1C 셀 모델입니다."
)


def save_and_download(fig: plt.Figure, filename: str) -> bytes:
    """Save matplotlib figure as PNG and return BytesIO for download button."""
    filepath = IMAGE_DIR / filename
    fig.savefig(filepath, dpi=150, bbox_inches="tight")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


def chart_download_col(fig: plt.Figure, filename: str) -> bytes:
    """Convenience: save chart and return bytes for st.download_button."""
    return save_and_download(fig, filename)


tab_arch, tab_cell = st.tabs(["🚌 DRAM 접근 시뮬레이터", "🔋 1T1C 셀 시각화"])

# =====================================================================
# Tab 1 — Architecture-level access simulator
# =====================================================================

with st.sidebar:
    st.header("DRAM 설정")
    st.caption("*DRAM 접근 시뮬레이터* 탭에 적용됩니다.")

    preset = st.selectbox("프리셋", ["DDR4-2400 유사", "커스텀"])
    if preset == "DDR4-2400 유사":
        trcd, tcl, trp, tras = 14, 14, 14, 33
    else:
        trcd = st.slider("tRCD (ACT→RD/WR)", 1, 40, 14)
        tcl = st.slider("tCL (RD/WR→data)", 1, 40, 14)
        trp = st.slider("tRP (PRE→ACT)", 1, 40, 14)
        tras = st.slider("tRAS (ACT→PRE 최소)", 1, 80, 33)
    st.caption(f"tRC = tRAS + tRP = **{tras + trp}** 사이클")

    num_banks = st.select_slider("랭크당 뱅크 수", options=[2, 4, 8, 16], value=8)

    st.header("스케줄링")
    policy = st.radio("정책", ["FCFS", "FR-FCFS"], index=1, horizontal=True)
    compare = st.checkbox("두 정책 비교하기", value=True)

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
    st.subheader("1. Trace 입력")
    uploaded = st.file_uploader("Trace CSV 업로드 (`cycle,address,op`)", type=["csv"])
    if uploaded is not None:
        trace = load_trace(io.TextIOWrapper(uploaded, encoding="utf-8"))
        st.success(f"`{uploaded.name}`에서 요청 {len(trace)}개를 불러왔습니다.")
    else:
        trace = load_trace(SAMPLE_TRACE)
        st.info(f"기본 제공 샘플 trace를 사용 중입니다 (요청 {len(trace)}개). CSV를 업로드하면 교체됩니다.")

    policies = ["FCFS", "FR-FCFS"] if compare else [policy]
    sims = {p: run_simulation(config, trace, p) for p in policies}
    main = sims[policy] if policy in sims else sims[policies[0]]

    st.subheader("2. 요약")
    if compare:
        cols = st.columns(len(policies))
        for col, p in zip(cols, policies):
            s = sims[p].stats
            with col:
                st.markdown(f"**{p}**")
                m1, m2 = st.columns(2)
                m1.metric("총 사이클", s.total_cycles)
                m2.metric("평균 지연시간", f"{s.avg_latency:.1f}")
                m3, m4 = st.columns(2)
                m3.metric("Row-hit 비율", f"{s.hit_rate:.0%}")
                m4.metric("Hit / Miss / Conf", f"{s.hits} / {s.misses} / {s.conflicts}")
    else:
        s = main.stats
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("총 사이클", s.total_cycles)
        m2.metric("평균 지연시간 (사이클)", f"{s.avg_latency:.1f}")
        m3.metric("Row-hit 비율", f"{s.hit_rate:.0%}")
        m4.metric("Hit / Miss / Conflict", f"{s.hits} / {s.misses} / {s.conflicts}")

    df = results_frame(main)
    st.subheader(f"3. 시각화 — {main.stats.policy}")

    left, right = st.columns(2)
    with left:
        st.markdown("**요청별 row-buffer 결과**")
        outcome = (
            df["result"].value_counts().reindex(["hit", "miss", "conflict"]).fillna(0).reset_index()
        )
        outcome.columns = ["result", "count"]
        st.altair_chart(
            alt.Chart(outcome)
            .mark_bar()
            .encode(
                x=alt.X("result", sort=["hit", "miss", "conflict"], title="결과"),
                y=alt.Y("count", title="요청 수"),
                color=alt.Color("result", scale=OUTCOME_COLORS, legend=None),
            ),
            use_container_width=True,
        )
        # Save matplotlib version
        fig, ax = plt.subplots(figsize=(6, 4))
        colors = {"hit": "#2ecc71", "miss": "#f1c40f", "conflict": "#e74c3c"}
        color_list = [colors[r] for r in outcome["result"]]
        ax.bar(outcome["result"], outcome["count"], color=color_list)
        ax.set_xlabel("Outcome")
        ax.set_ylabel("Requests")
        ax.set_title("Row-Buffer Hit/Miss/Conflict")
        buf = chart_download_col(fig, "row_buffer_hit_rate.png")
        plt.close(fig)
        st.download_button("📥 차트 다운로드", buf, "row_buffer_hit_rate.png", "image/png", key="hit_rate")

    with right:
        st.markdown("**뱅크 사용률 (뱅크별 접근 횟수)**")
        bank_df = df.groupby("bank").size().reset_index(name="accesses")
        st.altair_chart(
            alt.Chart(bank_df).mark_bar().encode(
                x=alt.X("bank:O", title="뱅크"), y=alt.Y("accesses", title="접근 횟수")
            ),
            use_container_width=True,
        )
        # Save matplotlib version
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar(bank_df["bank"].astype(str), bank_df["accesses"], color="#3498db")
        ax.set_xlabel("Bank")
        ax.set_ylabel("Accesses")
        ax.set_title("Bank Utilization")
        buf = chart_download_col(fig, "bank_utilization.png")
        plt.close(fig)
        st.download_button("📥 차트 다운로드", buf, "bank_utilization.png", "image/png", key="bank_util")

    st.markdown("**요청별 지연시간** (row-buffer 결과별 색상)")
    st.altair_chart(
        alt.Chart(df)
        .mark_circle(size=90)
        .encode(
            x=alt.X("req_id", title="요청 ID (trace 순서)"),
            y=alt.Y("latency", title="지연시간 (사이클)"),
            color=alt.Color("result", scale=OUTCOME_COLORS),
            tooltip=["req_id", "address", "op", "bank", "row", "result", "latency"],
        ),
        use_container_width=True,
    )
    # Save matplotlib version
    fig, ax = plt.subplots(figsize=(10, 5))
    colors_map = {"hit": "#2ecc71", "miss": "#f1c40f", "conflict": "#e74c3c"}
    for result_type in ["hit", "miss", "conflict"]:
        mask = df["result"] == result_type
        ax.scatter(df[mask]["req_id"], df[mask]["latency"],
                  label=result_type, color=colors_map[result_type], s=60, alpha=0.7)
    ax.set_xlabel("Request ID (trace order)")
    ax.set_ylabel("Latency (cycles)")
    ax.set_title("Latency per Request")
    ax.legend()
    ax.grid(True, alpha=0.3)
    buf = chart_download_col(fig, "latency_histogram.png")
    plt.close(fig)
    st.download_button("📥 차트 다운로드", buf, "latency_histogram.png", "image/png", key="latency")

    st.markdown("**명령어 타임라인** (점 하나 = command bus에 발행된 DRAM 명령어 1개)")
    cmd_df = commands_frame(main)
    st.altair_chart(
        alt.Chart(cmd_df)
        .mark_point(size=80, filled=True)
        .encode(
            x=alt.X("cycle", title="사이클"),
            y=alt.Y("bank", title="뱅크"),
            color=alt.Color("command", title="명령어"),
            shape="command",
            tooltip=["cycle", "command", "bank", "row"],
        ),
        use_container_width=True,
    )
    # Save matplotlib version
    if not cmd_df.empty:
        fig, ax = plt.subplots(figsize=(12, 5))
        cmd_colors = {"ACTIVATE": "#e67e22", "READ": "#3498db", "WRITE": "#9b59b6", "PRECHARGE": "#e74c3c"}
        for cmd in cmd_df["command"].unique():
            mask = cmd_df["command"] == cmd
            y_vals = cmd_df[mask]["bank"].str.extract(r"Bank (\d+)")[0].astype(int)
            ax.scatter(cmd_df[mask]["cycle"], y_vals, label=cmd,
                      color=cmd_colors.get(cmd, "#95a5a6"), s=80, alpha=0.7)
        ax.set_xlabel("Cycle")
        ax.set_ylabel("Bank")
        ax.set_title("Command Timeline")
        ax.legend()
        ax.grid(True, alpha=0.3)
        buf = chart_download_col(fig, "command_timeline.png")
        plt.close(fig)
        st.download_button("📥 차트 다운로드", buf, "command_timeline.png", "image/png", key="cmd_timeline")

    with st.expander("요청별 상세 테이블"):
        st.dataframe(df, use_container_width=True, hide_index=True)

    if compare:
        other = "FCFS" if main.stats.policy == "FR-FCFS" else "FR-FCFS"
        a, b = sims[main.stats.policy].stats, sims[other].stats
        st.info(
            f"**{a.policy}**는 **{a.total_cycles}** 사이클, row-hit 비율 **{a.hit_rate:.0%}**로 끝났고, "
            f"**{b.policy}**는 {b.total_cycles} 사이클, hit 비율 {b.hit_rate:.0%}입니다. "
            "FR-FCFS는 대기 중인 요청을 재정렬해 열린 row buffer를 최대한 활용합니다."
        )

# =====================================================================
# Tab 2 — 1T1C cell-level visualizer
# =====================================================================

with tab_cell:
    st.subheader("1개의 트랜지스터 + 1개의 커패시터 (1T1C)")
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
            "DRAM의 1비트는 access transistor 하나 뒤에 있는 **작은 커패시터의 전하**로 저장됩니다. "
            "이 커패시터는 **전하가 새기(leak) 때문에** 저장된 1이 점점 0으로 바랩니다 — 이것이 DRAM이 "
            "*dynamic*하며 **주기적으로 refresh해야 하는** 이유입니다. 읽기는 **파괴적(destructive)**입니다: "
            "셀은 자신의 전하를 bitline과 공유하고, **sense amplifier**가 이를 0 또는 1로 판별한 뒤, "
            "**restore** 과정이 그 값을 원래 세기로 다시 기록합니다."
        )

    st.markdown("#### 시뮬레이션 조작")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        tau = st.slider("Retention time τ (시간 단위)", 5.0, 200.0, 40.0, step=5.0,
                        help="누설 시상수: q(t) = q₀·e^(−t/τ)")
    with c2:
        use_refresh = st.checkbox("Refresh 활성화", value=True)
        refresh_iv = st.slider("Refresh 주기", 5.0, 150.0, 25.0, step=5.0,
                               disabled=not use_refresh)
    with c3:
        reads_text = st.text_input("Read 시점 (쉼표로 구분)", "18, 55")
    with c4:
        share = st.slider("Read 시 전하 공유 손실률", 0.0, 0.6, 0.35, step=0.05,
                          help="한 번 읽을 때 bitline으로 빠져나가는 셀 전하의 비율")
        threshold = st.slider("Sense threshold", 0.2, 0.8, 0.5, step=0.05)

    t_end = 100.0
    try:
        read_times = [float(x) for x in reads_text.replace(" ", "").split(",") if x]
    except ValueError:
        st.error("Read 시점은 숫자여야 합니다. 예: `18, 55`")
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
    m1.metric("Refresh 실행 횟수", with_refresh.refresh_count)
    m2.metric("Retention failure 횟수", with_refresh.failure_count,
              delta=None if with_refresh.failure_count == 0 else "데이터 손실!",
              delta_color="inverse")
    m3.metric("최종 감지값", with_refresh.final_value)
    m4.metric("Refresh 없을 때 최종값", no_refresh.final_value)

    if with_refresh.failure_count:
        st.error(
            "⚠️ 다음 refresh/read가 오기 **전에** 전하가 sense threshold 아래로 떨어졌습니다 — "
            "sense amplifier가 0으로 잘못 읽고 그 값을 그대로 복원했습니다. "
            "Refresh 주기를 줄이거나 τ를 늘려보세요."
        )
    elif use_refresh and no_refresh.final_value != 1:
        st.success(
            "✅ Refresh 덕분에 저장된 1이 유지되고 있습니다. Refresh가 없다면 같은 셀은 "
            "threshold 아래로 감쇠해 비트가 손실됩니다 (점선 참고)."
        )

    # ---- Charge vs time chart ----
    scenario_frames = [
        pd.DataFrame(with_refresh.samples, columns=["time", "charge"]).assign(
            scenario="refresh 있음" if use_refresh else "refresh 없음 (선택됨)")
    ]
    if use_refresh:
        scenario_frames.append(
            pd.DataFrame(no_refresh.samples, columns=["time", "charge"]).assign(
                scenario="refresh 없음")
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
            x=alt.X("time", title="시간 (단위)"),
            y=alt.Y("charge", title="커패시터 전하 (정규화)",
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
                    title="이벤트",
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
        "실선: 현재 설정에서의 셀 전하. 회색 점선: 같은 셀에서 refresh를 껐을 때. "
        "파란 세로선은 refresh 시점을 표시하고, 주황색은 destructive read(전하 공유), "
        "초록색은 sense-amp restore, 빨간색은 retention failure(잘못된 값이 감지되어 복원됨)를 나타냅니다."
    )

    # Save matplotlib version of charge decay chart
    fig, ax = plt.subplots(figsize=(12, 6))
    sample_with = pd.DataFrame(with_refresh.samples, columns=["time", "charge"])
    sample_no = pd.DataFrame(no_refresh.samples, columns=["time", "charge"])
    ax.plot(sample_with["time"], sample_with["charge"], "b-", linewidth=2, label="with refresh")
    ax.plot(sample_no["time"], sample_no["charge"], "gray", linewidth=2, linestyle="--", label="without refresh")
    ax.axhline(y=threshold, color="#7f8c8d", linestyle="--", linewidth=1.5, label=f"sense threshold ({threshold})")
    ax.set_xlabel("Time (units)")
    ax.set_ylabel("Capacitor charge (normalized)")
    ax.set_ylim([0, 1.05])
    ax.set_title("1T1C Cell Charge Decay with Refresh")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    buf = chart_download_col(fig, "one_t_one_c_charge_decay.png")
    plt.close(fig)
    st.download_button("📥 차트 다운로드", buf, "one_t_one_c_charge_decay.png", "image/png", key="charge_decay")

    # ---- Refresh overhead ----
    st.markdown("#### 시스템 레벨에서의 refresh overhead")
    o1, o2, o3 = st.columns([1, 1, 2])
    with o1:
        t_refi = st.number_input("tREFI (refresh 간 사이클)", 100, 50000, 9360, step=100)
    with o2:
        t_rfc = st.number_input("tRFC (refresh 1회당 사이클)", 10, 5000, 420, step=10)
    overhead = refresh_overhead(int(t_rfc), int(t_refi))
    with o3:
        st.metric("랭크가 refresh로 바쁜 비율", f"{overhead:.1%}",
                  help="tRFC / tREFI — 랭크가 요청을 처리할 수 없는 시간 비율")
        st.progress(min(overhead, 1.0))

    with st.expander("DRAM은 왜 refresh가 필요할까? (설명)"):
        st.markdown(
            """
**1. 커패시터는 전하가 샙니다.** DRAM 셀은 단 몇 femtofarad 크기의 커패시터에 전하로 비트를
저장합니다. 접합부(junction)와 게이트의 누설 전류가 이 전하를 빼내기 때문에, 저장된 **1**은
지수적으로 감쇠합니다 — 여기서는 `q(t) = q₀ · e^(−t/τ)`로 모델링했습니다. 전하가 sense
amplifier가 0과 구분할 수 있는 수준 아래로 떨어지면, 그 비트는 **아무 경고 없이 사라집니다.**

**2. Refresh는 곧 정해진 시간 안의 read + restore입니다.** 전하가 sense threshold를 넘기
전에, DRAM은 내부적으로 각 row를 *감지(sense)*하고 원래 세기로 *다시 기록(rewrite)*합니다.
JEDEC 표준은 모든 셀이 자신의 retention window 안에서 refresh되도록 요구합니다
(예: 64ms — row 하나당 **tREFI ≈ 7.8µs**마다 refresh 명령 1회).

**3. 읽기는 파괴적입니다.** Access transistor를 열면 셀의 전하가 bitline과 공유되며,
읽는 행위 자체가 저장된 값을 약화시킵니다. Sense amplifier는 이 작은 bitline 변화를
증폭해 깔끔한 0/1로 만들고, 그 값을 셀에 다시 복원합니다 (이것이 `tRAS`가 존재하는
이유이기도 합니다: restore가 끝날 때까지 row가 충분히 오래 열려 있어야 합니다).

**4. Refresh는 공짜가 아닙니다.** 랭크가 refresh(tRFC)를 실행하는 동안에는 메모리 요청을
처리할 수 없습니다 — 대역폭/지연시간에 대략 `tRFC / tREFI` 만큼의 세금이 붙는 셈입니다
(DDR4 기준 약 4~5%이며, 용량이 커질수록 명령 하나당 refresh해야 할 row가 늘어나 이 비율도 커집니다).

**직접 해보세요:** refresh 주기를 `τ·ln(1/threshold)` ≈ 전하가 threshold를 넘어서는 시점보다
*더 길게* 설정해보세요 — 다음 refresh는 0을 감지해서 잘못된 값을 그대로 복원하고, 빨간
*retention failure* 마커가 나타납니다. 이것이 실제 DRAM이 제때 refresh해서 막고 있는
바로 그 데이터 손실입니다.
            """
        )
