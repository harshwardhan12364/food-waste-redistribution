from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

from matching import InvalidTransition, RedistributionSystem, StorageError, ValidationError


st.set_page_config(page_title="FoodShare | Food Rescue", page_icon="🥬", layout="wide")


@st.cache_resource
def get_system(database_path: str) -> RedistributionSystem:
    return RedistributionSystem(database_path)


try:
    system = get_system(str(Path(__file__).with_name("foodshare.db")))
except StorageError as error:
    st.error(f"FoodShare could not open its local database: {error}")
    st.stop()

st.session_state.setdefault("submission_keys", {})
st.session_state.setdefault("donor_name", "")
st.session_state.setdefault("last_request_id", None)
st.session_state.setdefault("current_user", None)
st.session_state.setdefault("current_user_name", "")
st.session_state.setdefault("page", "Dashboard")


def navigate_to(page_name: str) -> None:
    st.session_state["page"] = page_name


def submission_key(kind: str, *values: object) -> str:
    fingerprint = hashlib.sha256(repr((kind, values)).encode("utf-8")).hexdigest()
    keys = st.session_state["submission_keys"]
    if fingerprint not in keys:
        keys[fingerprint] = uuid.uuid4().hex
    return keys[fingerprint]


def render_food_listing(item) -> None:
    food_emojis = {"prepared": "🍛", "bakery": "🥐", "produce": "🥕", "dairy": "🥛", "other": "🍱"}
    hours = item.hours_left
    time_left = f"{hours:.0f} hours" if hours >= 1 else f"{max(1, int(hours * 60))} min"
    st.markdown(
        f"""
        <div class="food-card">
          <div class="food-emoji">{food_emojis.get(item.category, "🍱")}</div>
          <div>
            <div class="food-name">{item.food}</div>
            <div class="food-meta">Donated by {item.donor} · {item.category.title()}</div>
            <div class="food-meta">📍 {item.location} · {item.available_meals} of {item.meals} meals available · ⏱ {time_left} left</div>
            <div class="food-meta">Pickup: {item.pickup_instructions or "Contact donor to arrange collection"}</div>
          </div>
          <span class="food-badge">{item.status}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Manrope:wght@500;600;700;800&display=swap');
        :root { --forest:#063d33; --forest-deep:#032d27; --green:#0ba873; --mint:#e9f8ef; --ink:#15352e; --muted:#60766f; --line:#dcece5; }
        html, body, [class*="css"] { font-family:'DM Sans',sans-serif; color:var(--ink); }
        .stApp { background:#f6faf8; }
        .block-container { max-width:1600px; padding:2rem 1.2rem 2rem; }
        h1,h2,h3,h4 { font-family:'Manrope',sans-serif !important; color:var(--ink); }
        .auth-shell { max-width: 960px; margin: 0 auto; padding: 2rem 1.25rem; }
        .auth-card { background: white; border: 1px solid #dfece7; border-radius: 18px; box-shadow: 0 10px 30px rgba(6, 61, 51, 0.08); padding: 2rem; }
        .auth-title { font-size: 1.9rem; line-height:1.2; font-weight: 800; color: #123d34; margin-bottom: 0.35rem; }
        .auth-subtitle { color: #597269; margin-bottom: 1rem; }
        .auth-pill { display: inline-block; padding: 0.35rem 0.7rem; border-radius: 999px; background: #e8f8ef; color: #0d7a56; font-weight: 700; font-size: 0.72rem; margin-bottom: 1.25rem; }
        .banner { display:flex; align-items:center; gap:18px; min-height:82px; margin:0 -1.1rem 10px; padding:10px 3%; color:white; background:linear-gradient(105deg,#04392f 0%,#075746 72%,#08705a 100%); }
        .logo { font-size:2.55rem; line-height:1; }
        .banner h1 { margin:0; color:white; font-size:1.25rem; line-height:1.2; font-weight:800; }
        .banner h1 span { color:#8eea70; }
        .banner p { margin:3px 0 0; color:#d5eee4; font-size:.84rem; }
        .banner-note { margin-left:auto; color:#d5eee4; font-size:.72rem; white-space:nowrap; }
        .outer { display:grid; grid-template-columns:minmax(230px, .27fr) minmax(0, .73fr); gap:12px; align-items:start; }
        .info-card { border:1px solid #d5eee2; border-radius:9px; background:#fff; padding:14px 15px; margin-bottom:10px; box-shadow:0 2px 8px #12433108; }
        .info-title { font-family:'Manrope',sans-serif; font-size:.95rem; font-weight:800; margin:0 0 8px; color:#16473b; }
        .info-copy { color:#3e5b52; font-size:.75rem; line-height:1.55; margin:0; }
        .info-list { color:#3e5b52; font-size:.72rem; line-height:1.45; margin:0; padding:0; list-style:none; }
        .info-list li { margin:7px 0; }
        .check { color:#14865f; font-weight:800; margin-right:6px; }
        .step { display:inline-grid; place-items:center; width:19px; height:19px; margin-right:7px; color:#fff; background:#14956d; border-radius:50%; font-weight:800; font-size:.67rem; }
        .app-shell { border:1px solid #b9d9cc; border-radius:12px; background:#fff; padding:9px; box-shadow:0 5px 18px #063d3310; }
        .rail { background:linear-gradient(180deg,var(--forest) 0%,var(--forest-deep) 100%); border-radius:8px 8px 0 0; min-height:80px; padding:14px 10px 8px; color:white; }
        .rail-brand { font-family:'Manrope',sans-serif; font-weight:800; font-size:1.04rem; }
        .rail-tag { color:#afd0c3; font-size:.65rem; margin:2px 0 16px; }
        .rail-label { color:#93b7a9; font-size:.63rem; margin:14px 8px 6px; text-transform:uppercase; }
        .rail-foot { margin-top:0; padding:18px 8px; background:var(--forest-deep); border-radius:0 0 8px 8px; text-align:center; color:#b5d5c7; font-size:.69rem; line-height:1.4; }
        .welcome { display:flex; align-items:center; gap:10px; padding:6px 8px 12px; border-bottom:1px solid #e8f0ed; margin-bottom:10px; }
        .welcome-icon { font-size:1.4rem; }
        .welcome-title { font-weight:800; font-size:.84rem; }
        .welcome-sub { color:#668078; font-size:.66rem; }
        .stat { min-height:83px; padding:12px 13px; border-radius:8px; color:white; }
        .stat-label { font-size:.68rem; opacity:.95; }
        .stat-value { font-family:'Manrope',sans-serif; font-size:1.55rem; line-height:1.15; font-weight:800; }
        .stat-note { font-size:.61rem; opacity:.9; }
        .stat-green { background:linear-gradient(125deg,#0bb379,#15a77b); }
        .stat-blue { background:linear-gradient(125deg,#1594ee,#388cea); }
        .stat-orange { background:linear-gradient(125deg,#ffa21e,#f79318); }
        .stat-purple { background:linear-gradient(125deg,#8e5de6,#8055d9); }
        .panel-title { display:flex; align-items:center; justify-content:space-between; margin:12px 0 7px; font-size:.83rem; font-weight:800; }
        .panel { border:1px solid #dcece5; border-radius:8px; background:white; padding:10px; min-height:180px; }
        .food-card { display:grid; grid-template-columns:37px minmax(0,1fr) auto; align-items:center; gap:8px; background:#fff; border:1px solid #e7efeb; border-radius:7px; padding:8px; margin-bottom:7px; }
        .food-emoji { display:grid; place-items:center; width:35px; height:35px; border-radius:8px; background:#fff2df; font-size:1.15rem; }
        .food-name { font-size:.69rem; font-weight:800; }
        .food-meta { font-size:.59rem; color:#6d827a; margin-top:2px; }
        .food-badge { background:#e1f7e9; color:#18774c; border-radius:10px; padding:3px 6px; font-size:.55rem; font-weight:800; }
        .match-card { border:1px solid #d9eee8; border-radius:7px; padding:9px; background:#f8fffc; margin-bottom:7px; }
        .match-top { display:flex; justify-content:space-between; gap:8px; align-items:center; font-size:.66rem; font-weight:700; }
        .match-sub { color:#687f76; font-size:.58rem; margin:3px 0 6px; }
        .quick { background:#fff; border:1px solid #dcece5; border-radius:8px; padding:10px; margin-bottom:8px; }
        .quick h4 { font-size:.75rem; margin:0 0 7px; }
        .util-ring { display:grid; place-items:center; width:68px; height:68px; border-radius:50%; margin:auto; background:conic-gradient(#09ab77 0 68%,#dcefe8 68% 100%); position:relative; }
        .util-ring:before { content:''; position:absolute; width:49px; height:49px; border-radius:50%; background:#fff; }
        .util-ring span { z-index:1; font-weight:800; font-size:.73rem; }
        .dsa { margin-top:10px; border:1px solid #d5eee2; border-radius:10px; padding:12px; background:linear-gradient(110deg,#f3fbf7,#fff); }
        .dsa-head { font-family:'Manrope',sans-serif; font-weight:800; font-size:1rem; color:#174b3e; }
        .dsa-copy { color:#617a70; font-size:.68rem; margin:4px 0 9px; }
        .dsa-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:7px; }
        .dsa-item { min-height:104px; padding:9px; border-radius:7px; border:1px solid #dcece5; background:#fff; }
        .dsa-icon { font-size:1rem; }
        .dsa-name { font-size:.65rem; font-weight:800; margin:4px 0; }
        .dsa-desc { color:#60776e; font-size:.57rem; line-height:1.4; }
        .stButton > button { border-radius:6px; font-weight:700; }
        .stButton > button[kind="primary"] { background:#0b8d68; border-color:#0b8d68; color:white; }
        [data-testid="stRadio"] input { accent-color:#0b8d68; }
        @media (max-width: 1000px) { .outer { grid-template-columns:1fr; } .dsa-grid { grid-template-columns:repeat(3,minmax(0,1fr)); } .banner h1 { font-size:1.3rem; } }
        @media (max-width: 650px) { .block-container { padding:0 .7rem 1.3rem; } .banner { margin:0 -.7rem 9px; padding:12px; gap:9px; } .banner h1 { font-size:1rem; } .banner p,.banner-note { font-size:.62rem; } .logo { font-size:1.8rem; } .dsa-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_auth_page() -> None:
    left_space, auth_column, right_space = st.columns([1, 1.15, 1])
    with auth_column:
        with st.container(border=True):
            st.markdown('<div class="auth-pill">FoodShare</div>', unsafe_allow_html=True)
            st.markdown('<div class="auth-title">Welcome to FoodShare</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="auth-subtitle">Sign in to manage donations, match requests, and reduce food waste.</div>',
                unsafe_allow_html=True,
            )
            auth_view = st.radio(
                "Choose an option", ["Login", "Sign up"], horizontal=True, label_visibility="collapsed"
            )

            if auth_view == "Login":
                with st.form("login_form", clear_on_submit=False):
                    email = st.text_input("Email address", placeholder="you@example.com")
                    password = st.text_input("Password", type="password", placeholder="Enter your password")
                    if st.form_submit_button("Login", type="primary", use_container_width=True):
                        try:
                            user = system.authenticate_user(email, password)
                            st.session_state["current_user"] = user.email
                            st.session_state["current_user_name"] = user.name
                            st.rerun()
                        except (ValidationError, StorageError) as error:
                            st.error(str(error))
            else:
                with st.form("signup_form", clear_on_submit=False):
                    name = st.text_input("Full name", placeholder="Jane Doe")
                    email = st.text_input("Email address", placeholder="jane@example.com")
                    password = st.text_input("Password", type="password", placeholder="Minimum 8 characters")
                    confirm_password = st.text_input("Confirm password", type="password", placeholder="Repeat your password")
                    if st.form_submit_button("Create account", type="primary", use_container_width=True):
                        if password != confirm_password:
                            st.error("Passwords do not match.")
                        else:
                            try:
                                user = system.register_user(email, password, name)
                                st.session_state["current_user"] = user.email
                                st.session_state["current_user_name"] = user.name
                                st.rerun()
                            except (ValidationError, StorageError) as error:
                                st.error(str(error))


def render_dashboard() -> None:
    try:
        available = sorted(system.available_food, key=lambda item: item.expires_at)
        all_food = system.all_food
        requests = system.requests
        active_requests = [request for request in requests if request.status in {"Submitted", "Matched", "Confirmed"}]
        matched_requests = [request for request in requests if request.status in {"Matched", "Confirmed"}]
        rejected_requests = [request for request in requests if request.status == "Rejected"]
        meals_available = sum(item.available_meals for item in available)
        matches_count = sum(request.status in {"Matched", "Confirmed", "Completed"} for request in requests)
        completed_meals = sum(request.meals for request in requests if request.status == "Completed")
    except StorageError as error:
        st.error(f"FoodShare could not load its saved records: {error}")
        st.stop()

    st.markdown(
        '<div class="banner"><div class="logo">🥗</div><div><h1>Food Waste Reduction &amp; <span>Surplus Food Redistribution System</span></h1><p>Good food should feed people, not landfills.</p></div><div class="banner-note">Less Waste • More Smiles • 🌱</div></div>',
        unsafe_allow_html=True,
    )

    if st.button("Logout"):
        st.session_state["current_user"] = None
        st.session_state["current_user_name"] = ""
        st.rerun()

    left_column, right_column = st.columns([0.27, 0.73], gap="small")
    with left_column:
        st.markdown('<div class="info-card"><div class="info-title">💡 Project Overview</div><p class="info-copy">This system helps restaurants, college canteens, events and stores record surplus food and match it with organizations or people nearby who need it. It reduces food waste and helps good food reach those who need it.</p></div>', unsafe_allow_html=True)
        st.markdown('<div class="info-card"><div class="info-title">✦ Key Features</div><ul class="info-list"><li><span class="check">●</span>Add surplus food with quantity, expiry and location</li><li><span class="check">●</span>Add food requests from organizations and people</li><li><span class="check">●</span>Automatic matching by category, quantity and expiry</li><li><span class="check">●</span>Real-time match status and activity history</li><li><span class="check">●</span>Track donations and distributions</li></ul></div>', unsafe_allow_html=True)
        st.markdown('<div class="info-card"><div class="info-title">⚙ How It Works</div><ul class="info-list"><li><span class="step">1</span>Donors add available surplus food.</li><li><span class="step">2</span>Organizations add what they need.</li><li><span class="step">3</span>The system matches suitable food and requests.</li><li><span class="step">4</span>Both parties are notified of the match.</li><li><span class="step">5</span>Food is collected and marked delivered.</li></ul><div style="margin-top:12px;padding:8px;background:#e7f8e9;border-radius:6px;text-align:center;color:#267b50;font-size:.68rem;font-weight:700">🌿 Real food. Real impact.</div></div>', unsafe_allow_html=True)

    with right_column:
        shell_rail, shell_main = st.columns([0.17, 0.83], gap="small")
        with shell_rail:
            st.markdown('<div class="rail"><div class="rail-brand">🍃 FoodShare</div><div class="rail-tag">Save Food · Help People</div><div class="rail-label">Workspace</div></div>', unsafe_allow_html=True)
            page = st.radio("Workspace", ["Dashboard", "Donor Page", "Find Food", "View Matches", "History"], label_visibility="collapsed", key="page")
            st.markdown('<div class="rail-foot">Together for a<br>hunger-free tomorrow ♥</div>', unsafe_allow_html=True)

        with shell_main:
            user_name = st.session_state.get("current_user_name") or "Food Hero"
            st.markdown(f'<div class="welcome"><div class="welcome-icon">☀️</div><div><div class="welcome-title">Hello, {user_name}!</div><div class="welcome-sub">Let’s reduce food waste and feed people.</div></div></div>', unsafe_allow_html=True)

            if page == "Dashboard":
                stat_cols = st.columns(4, gap="small")
                stats = [
                    ("Surplus Food Available", meals_available, "unallocated meals", "stat-green"),
                    ("Active Requests", len(active_requests), "requests", "stat-blue"),
                    ("Matches Found", matches_count, "requests matched", "stat-orange"),
                    ("Food Collected", completed_meals, "meals completed", "stat-purple"),
                ]
                for col, (label, value, note, color) in zip(stat_cols, stats):
                    with col:
                        st.markdown(f'<div class="stat {color}"><div class="stat-label">{label}</div><div class="stat-value">{value}</div><div class="stat-note">{note}</div></div>', unsafe_allow_html=True)

                food_col, match_col, action_col = st.columns([1.13, 1.08, 0.74], gap="small")
                with food_col:
                    st.markdown(f'<div class="panel-title">Recent Surplus Food <span style="color:#2786cd;font-size:.62rem">{len(available)} active</span></div><div class="panel">', unsafe_allow_html=True)
                    if available:
                        for item in available:
                            render_food_listing(item)
                    else:
                        st.caption("No active surplus listings.")
                    st.markdown("</div>", unsafe_allow_html=True)

                with match_col:
                    st.markdown('<div class="panel-title">Automatic Matches <span style="color:#2786cd;font-size:.62rem">Expiry prioritized</span></div><div class="panel">', unsafe_allow_html=True)
                    if matched_requests:
                        for request in matched_requests[:4]:
                            st.markdown(f'<div class="match-card"><div class="match-top"><span>🍱 {request.food or "Donation"}</span><span>{request.organization}</span></div><div class="match-sub">{request.meals} meals · {request.category.title()} · {request.location} · Status: {request.status} · Donor: {request.donor}</div></div>', unsafe_allow_html=True)
                    else:
                        st.caption("No active match yet.")
                    if rejected_requests:
                        rejected_request = rejected_requests[0]
                        st.markdown(f'<div class="match-card" style="background:#fff7f4;border-color:#f4d9d2"><div class="match-top"><span>Request rejected</span><span>{rejected_request.organization}</span></div><div class="match-sub">{rejected_request.rejection_reason}</div></div>', unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

                with action_col:
                    st.markdown('<div class="panel-title">Add New</div>', unsafe_allow_html=True)
                    st.markdown('<div class="quick"><h4>＋ Add Surplus Food</h4><div style="color:#6e8179;font-size:.61rem">Record available food</div></div>', unsafe_allow_html=True)
                    st.button("Add surplus food", key="quick-food", on_click=navigate_to, args=("Donor Page",), use_container_width=True)
                    st.markdown('<div class="quick"><h4>♧ Add Food Request</h4><div style="color:#6e8179;font-size:.61rem">Request needed food</div></div>', unsafe_allow_html=True)
                    st.button("Add food request", key="quick-request", on_click=navigate_to, args=("Find Food",), use_container_width=True)

            elif page == "Donor Page":
                st.markdown('<div class="panel-title">Donor Page · Share surplus food</div>', unsafe_allow_html=True)
                with st.form("food_form", clear_on_submit=False):
                    donor = st.text_input("Donor name", placeholder="e.g. Canteen A", key="donor_name")
                    food = st.text_input("Food available", placeholder="e.g. Vegetable curry")
                    form_cols = st.columns(2)
                    with form_cols[0]:
                        category = st.selectbox("Category", ["prepared", "produce", "bakery", "dairy", "other"])
                        meals = st.number_input("Meals", min_value=1, value=20, step=1)
                    expiry_default = datetime.now().astimezone() + timedelta(hours=4)
                    with form_cols[1]:
                        location = st.text_input("Neighborhood", placeholder="e.g. Downtown")
                        expiry_date = st.date_input("Expiry date", value=expiry_default.date())
                        expiry_time = st.time_input("Expiry time", value=expiry_default.time().replace(second=0, microsecond=0))
                    description = st.text_area("Food details", placeholder="Ingredients, packaging, dietary notes")
                    pickup_instructions = st.text_input("Pickup instructions", placeholder="Pickup point or contact details")
                    if st.form_submit_button("Add surplus food", type="primary", use_container_width=True):
                        if donor.strip() and food.strip() and location.strip():
                            try:
                                expiry_at = datetime.combine(expiry_date, expiry_time).astimezone()
                                listing = system.add_food(
                                    donor,
                                    food,
                                    category,
                                    int(meals),
                                    location,
                                    expiry_at,
                                    description,
                                    pickup_instructions,
                                    idempotency_key=submission_key(
                                        "donation",
                                        donor,
                                        food,
                                        category,
                                        int(meals),
                                        location,
                                        expiry_at.isoformat(),
                                        description,
                                        pickup_instructions,
                                    ),
                                )
                                st.session_state["last_donor_name"] = donor.strip()
                                st.session_state["request_notice"] = ("success", f"Listing #{listing.id} saved. It is now visible to requesters.")
                                st.rerun()
                            except (ValidationError, StorageError) as error:
                                st.error(str(error))
                        else:
                            st.error("Enter a donor name, food description, and neighborhood before submitting.")

                donor_filter = st.session_state.get("last_donor_name", st.session_state["donor_name"]).strip().casefold()
                donor_listings = [item for item in all_food if item.donor.casefold() == donor_filter]
                st.markdown(f'<div class="panel-title">Your listings <span style="color:#2786cd;font-size:.62rem">{len(donor_listings)}</span></div>', unsafe_allow_html=True)
                if donor_listings:
                    for item in donor_listings:
                        render_food_listing(item)
                else:
                    st.info("Submit a donation to create your first listing.")

            elif page == "Find Food":
                st.markdown(f'<div class="panel-title">Food available from local donors <span style="color:#2786cd;font-size:.62rem">{len(available)} listings</span></div>', unsafe_allow_html=True)
                st.caption("A match requires the same category and neighborhood, enough meals, and food that has not expired. The soonest-expiring suitable listing is selected automatically.")
                if available:
                    for item in available:
                        render_food_listing(item)
                else:
                    st.info("No donor food is currently listed. Check back later.")

                st.markdown('<div class="panel-title">Request food</div>', unsafe_allow_html=True)
                with st.form("request_form", clear_on_submit=False):
                    organization = st.text_input("Organization", placeholder="e.g. Community kitchen")
                    form_cols = st.columns(2)
                    with form_cols[0]:
                        requested_category = st.selectbox("Food category", ["prepared", "produce", "bakery", "dairy", "other"], key="request_category")
                        requested_meals = st.number_input("Meals needed", min_value=1, value=15, step=1)
                    with form_cols[1]:
                        request_location = st.text_input("Neighborhood", placeholder="e.g. Downtown", key="request_location")
                    if st.form_submit_button("Add food request", type="primary", use_container_width=True):
                        if organization.strip() and request_location.strip():
                            try:
                                request = system.add_request(
                                    organization,
                                    requested_category,
                                    int(requested_meals),
                                    request_location,
                                    idempotency_key=submission_key(
                                        "request",
                                        organization,
                                        requested_category,
                                        int(requested_meals),
                                        request_location,
                                    ),
                                )
                                st.session_state["last_request_id"] = request.id
                                st.rerun()
                            except (ValidationError, StorageError) as error:
                                st.error(str(error))
                        else:
                            st.error("Enter an organization name and neighborhood before submitting.")

                st.markdown("#### Your request status")
                tracked_requests = [request for request in system.requests if request.id == st.session_state.get("last_request_id")]
                if tracked_requests:
                    request = tracked_requests[0]
                    st.markdown(f"**Request #{request.id}: {request.status}** · {request.meals} {request.category} meals · {request.location}")
                    if request.status in {"Matched", "Confirmed"}:
                        st.markdown(f"Donation: **{request.food}** from **{request.donor}**. Pickup: {request.pickup_instructions or 'Contact donor to arrange collection'}.")
                    if request.status == "Matched":
                        action_col, cancel_col = st.columns(2)
                        with action_col:
                            if st.button("Confirm pickup", key=f"confirm-{request.id}", type="primary"):
                                try:
                                    system.confirm_request(request.id)
                                    st.rerun()
                                except (InvalidTransition, StorageError) as error:
                                    st.error(str(error))
                        with cancel_col:
                            if st.button("Cancel request", key=f"cancel-{request.id}"):
                                try:
                                    system.cancel_request(request.id)
                                    st.rerun()
                                except (InvalidTransition, StorageError) as error:
                                    st.error(str(error))
                    elif request.status == "Confirmed":
                        st.info("After the food has actually been collected, mark collection complete.")
                        if st.button("Mark collected", key=f"complete-{request.id}", type="primary"):
                            try:
                                system.complete_request(request.id)
                                st.rerun()
                            except (InvalidTransition, StorageError) as error:
                                st.error(str(error))
                    elif request.status == "Rejected":
                        st.warning(request.rejection_reason or "No eligible donation was available.")
                    elif request.status == "Completed":
                        st.success("Collection recorded as complete.")

            elif page == "View Matches":
                st.markdown('<div class="panel-title">Request and collection tracking</div>', unsafe_allow_html=True)
                if requests:
                    for request in requests:
                        with st.container(border=True):
                            st.markdown(f"**Request #{request.id} · {request.organization} · {request.status}**")
                            st.caption(f"{request.meals} {request.category} meals · {request.location} · {request.created_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')}")
                            if request.donation_id:
                                st.markdown(f"Donation: **{request.food}** from **{request.donor}** · Pickup: {request.pickup_instructions or 'Contact donor to arrange collection'}")
                            if request.rejection_reason:
                                st.warning(request.rejection_reason)
                            if request.status == "Matched":
                                confirm_col, cancel_col = st.columns(2)
                                with confirm_col:
                                    if st.button("Confirm pickup", key=f"track-confirm-{request.id}", type="primary"):
                                        try:
                                            system.confirm_request(request.id)
                                            st.rerun()
                                        except (InvalidTransition, StorageError) as error:
                                            st.error(str(error))
                                with cancel_col:
                                    if st.button("Cancel request", key=f"track-cancel-{request.id}"):
                                        try:
                                            system.cancel_request(request.id)
                                            st.rerun()
                                        except (InvalidTransition, StorageError) as error:
                                            st.error(str(error))
                            elif request.status == "Confirmed":
                                st.info("Confirm completion only after food has been collected.")
                                if st.button("Mark collected", key=f"track-complete-{request.id}", type="primary"):
                                    try:
                                        system.complete_request(request.id)
                                        st.rerun()
                                    except (InvalidTransition, StorageError) as error:
                                        st.error(str(error))
                else:
                    st.info("No requests have been submitted yet.")

            else:
                st.markdown('<div class="panel-title">Activity History · newest first</div>', unsafe_allow_html=True)
                if system.history:
                    for event in reversed(system.history):
                        st.markdown(f'<div class="match-card">{event}<div class="food-meta">Saved activity</div></div>', unsafe_allow_html=True)
                else:
                    st.caption("No activity yet.")

    dsa_items = [
        ("♧", "1. Queue", "Pending food requests are served in first-in, first-out order."),
        ("☷", "2. List", "Available surplus food items are stored for fast browsing."),
        ("▣", "3. Dictionary", "Category and location indexes help organize donations."),
        ("⌕", "4. Searching", "Find a request with suitable type, amount and location."),
        ("☷", "5. Sorting", "Prioritize the closest expiry time to reduce waste."),
        ("▤", "6. Stack", "Keep an operation history with the latest action on top."),
    ]
    st.markdown(
        '<div class="dsa"><div class="dsa-head">◉ How DSA Concepts Are Used</div><div class="dsa-copy">This project uses core Data Structures and Algorithms to coordinate food quickly and fairly.</div><div class="dsa-grid">'
        + ''.join(f'<div class="dsa-item"><div class="dsa-icon">{icon}</div><div class="dsa-name">{title}</div><div class="dsa-desc">{description}</div></div>' for icon, title, description in dsa_items)
        + '</div><div style="margin:8px auto 0;width:max-content;max-width:100%;padding:6px 18px;border-radius:20px;background:#064c3d;color:#e3f6e8;text-align:center;font-size:.62rem">🌿 DSA makes the system smarter | Python makes it work | Together we create real impact</div></div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    if st.session_state.get("current_user"):
        render_dashboard()
    else:
        render_auth_page()


if __name__ == "__main__":
    main()
