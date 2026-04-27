from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import streamlit as st


APP_DIR = Path(__file__).parent
DATA_PATH = APP_DIR / "champion_ultimate_cooldowns.json"
CACHE_PATH = APP_DIR / "cache.json"
RANK_OPTIONS = ["1", "2", "3", "All"]


st.set_page_config(
    page_title="Ultimate Cooldowns",
    page_icon="R",
    layout="wide",
    initial_sidebar_state="expanded",
)


def parse_cooldowns(value: str | None) -> list[float]:
    if not value:
        return []
    return [float(match) for match in re.findall(r"\d+(?:\.\d+)?", value)]


def adjusted_cooldown(base: float, ability_haste: float) -> float:
    return base * (100 / (100 + ability_haste))


def format_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    rounded = round(value, 1)
    if rounded.is_integer():
        return f"{int(rounded)}s"
    return f"{rounded:.1f}s"


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(data: dict) -> None:
    CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ah_key(name: str) -> str:
    return f"ability_haste::{name}"


def rk_key(name: str) -> str:
    return f"rank::{name}"


@st.cache_data(show_spinner=False)
def load_champions() -> list[dict[str, Any]]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    champions = []
    for champion in data.values():
        champions.append(
            {
                **champion,
                "cooldowns": parse_cooldowns(champion.get("ultimate_cooldown")),
            }
        )
    return sorted(champions, key=lambda c: c["name"])


def selected_rank_indices(cooldowns: list[float], rank_choice: str) -> list[int]:
    if not cooldowns:
        return []
    if rank_choice == "All":
        return list(range(len(cooldowns)))
    return [min(int(rank_choice) - 1, len(cooldowns) - 1)]


def cooldown_rows(champion: dict[str, Any], ability_haste: float, rank_choice: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for rank_index in selected_rank_indices(champion["cooldowns"], rank_choice):
        base = champion["cooldowns"][rank_index]
        rows.append(
            {
                "Rank": f"Rank {rank_index + 1}",
                "Base": format_seconds(base),
                "Adjusted": format_seconds(adjusted_cooldown(base, ability_haste)),
            }
        )
    return rows


def render_card(champion: dict[str, Any]) -> None:
    name = champion["name"]
    image_path = APP_DIR / str(champion.get("img_path", ""))

    with st.container(border=True):
        image_col, body_col = st.columns([0.25, 0.75], vertical_alignment="top")
        with image_col:
            if image_path.exists():
                st.image(str(image_path), width=58)
        with body_col:
            st.subheader(name)
            st.caption(champion.get("ultimate") or "Unknown ultimate")
            if champion["cooldowns"]:
                current_ah = st.session_state.get(ah_key(name), 0.0)
                current_rank = st.session_state.get(rk_key(name), "3")
                display_index = (
                    len(champion["cooldowns"]) - 1
                    if current_rank == "All"
                    else min(int(current_rank) - 1, len(champion["cooldowns"]) - 1)
                )
                base = champion["cooldowns"][display_index]
                st.metric("Adjusted CD", format_seconds(adjusted_cooldown(base, current_ah)))

        st.number_input("Ability haste", min_value=0.0, step=5.0, key=ah_key(name))
        st.radio("Rank", RANK_OPTIONS, horizontal=True, key=rk_key(name))

        rows = cooldown_rows(champion, st.session_state[ah_key(name)], st.session_state[rk_key(name)])
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.warning(champion.get("error") or "No direct cooldown found.")


# ── Startup: hydrate session state from cache ─────────────────────────────────
cache = load_cache()
for _k, _v in cache.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

champions = load_champions()
champion_by_name = {c["name"]: c for c in champions}
champion_names = [c["name"] for c in champions]

if "selected_champions" not in st.session_state:
    st.session_state.selected_champions = []

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Controls")
    default_ability_haste = st.number_input(
        "Default ability haste",
        min_value=0.0,
        value=0.0,
        step=5.0,
        help="Used when a champion does not already have its own ability haste value.",
    )
    default_rank = st.radio("Default rank", RANK_OPTIONS, horizontal=True)

    action_col_1, action_col_2 = st.columns(2)
    with action_col_1:
        if st.button("Select all", use_container_width=True):
            st.session_state.selected_champions = champion_names.copy()
    with action_col_2:
        if st.button("Clear", use_container_width=True):
            st.session_state.selected_champions = []

    selected_names = st.multiselect(
        "Champions",
        champion_names,
        key="selected_champions",
        placeholder="Search and select champions",
    )

    apply_col_1, apply_col_2 = st.columns(2)
    with apply_col_1:
        if st.button("Apply default AH", use_container_width=True):
            for n in selected_names:
                st.session_state[ah_key(n)] = default_ability_haste
    with apply_col_2:
        if st.button("Apply default rank", use_container_width=True):
            for n in selected_names:
                st.session_state[rk_key(n)] = default_rank

# ── Main ──────────────────────────────────────────────────────────────────────
selected_champions = [champion_by_name[n] for n in selected_names]
direct_cooldowns = [c for c in champions if c["cooldowns"]]

st.title("Ultimate Cooldowns")

metric_col_1, metric_col_2, metric_col_3 = st.columns(3)
metric_col_1.metric("Selected", len(selected_champions))
metric_col_2.metric("Default AH", f"{default_ability_haste:g}")
metric_col_3.metric("Direct cooldowns", f"{len(direct_cooldowns)} / {len(champions)}")

if not selected_champions:
    st.info("Select champions from the sidebar.")
else:
    st.caption("Cooldown is calculated as base * 100 / (100 + ability haste).")
    cols_per_row = 5
    for start in range(0, len(selected_champions), cols_per_row):
        columns = st.columns(cols_per_row)
        for column, champion in zip(columns, selected_champions[start : start + cols_per_row]):
            with column:
                render_card(champion)

# ── Shutdown: persist state to cache file ─────────────────────────────────────
cache_out: dict = {"selected_champions": list(st.session_state.get("selected_champions", []))}
for _k, _v in st.session_state.items():
    if isinstance(_k, str) and _k.startswith(("ability_haste::", "rank::")):
        cache_out[_k] = _v
save_cache(cache_out)
