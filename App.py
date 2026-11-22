"""
Pay-Per-Day Micro Insurance - PROTOTYPE
=======================================

A demo app where users switch insurance cover on only for the days they need it.

NOTE: This is a prototype for learning/demo purposes only.
  - Payments are simulated (no real money moves).
  - Claims are decided by simple rules, not by a real insurer.
  - Data is stored in a local JSON file.

Run it:
    pip install -r requirements.txt
    streamlit run app.py
"""

import json
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

import streamlit as st

# PDF certificates are optional; the app falls back to a .txt certificate.
try:
    from fpdf import FPDF

    PDF_AVAILABLE = True
except ImportError:  # pragma: no cover
    PDF_AVAILABLE = False


# --------------------------------------------------------------------------
# Configuration (tweak these numbers to experiment)
# --------------------------------------------------------------------------
CURRENCY = "PKR"
WAITING_HOURS = 2          # cover starts this many hours after purchase (anti-fraud)
DEDUCTIBLE_PCT = 0.10      # customer bears 10% of every approved claim
MIN_PREMIUM = 50           # minimum total premium per policy
REVIEW_THRESHOLD = 0.50    # claims above 50% of item value go to manual review
DATA_FILE = Path("data_store.json")

# Daily risk rate = fraction of item value charged per day of cover.
CATEGORIES = {
    "Phone": {
        "rate": 0.0015,
        "max_value": 300_000,
        "covered": ["Theft", "Accidental damage", "Screen break"],
    },
    "Laptop": {
        "rate": 0.0012,
        "max_value": 500_000,
        "covered": ["Theft", "Accidental damage", "Liquid damage"],
    },
    "Bike": {
        "rate": 0.0020,
        "max_value": 400_000,
        "covered": ["Theft", "Accident damage"],
    },
    "Camera": {
        "rate": 0.0014,
        "max_value": 400_000,
        "covered": ["Theft", "Accidental damage", "Liquid damage"],
    },
    "Travel Baggage": {
        "rate": 0.0005,
        "max_value": 200_000,
        "covered": ["Theft", "Loss by carrier", "Damage in transit"],
    },
}

# Always excluded in this demo, shown to the user so they can see a rejection.
NOT_COVERED_EVENTS = ["Normal wear and tear", "Intentional damage"]

RISK_LEVELS = {
    "Normal (home city, everyday use)": 1.0,
    "Higher (crowded areas, events)": 1.3,
    "Travel abroad": 1.6,
}

PAYMENT_METHODS = ["Mock Wallet", "Mock Debit/Credit Card"]


# --------------------------------------------------------------------------
# Storage helpers (JSON file)
# --------------------------------------------------------------------------
def load_store() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"policies": [], "claims": []}


def save_store(store: dict) -> None:
    try:
        DATA_FILE.write_text(json.dumps(store, indent=2), encoding="utf-8")
    except OSError:
        st.warning("Could not save data to disk (running read-only?). Data lives in memory only.")


def get_store() -> dict:
    if "store" not in st.session_state:
        st.session_state.store = load_store()
    return st.session_state.store


# --------------------------------------------------------------------------
# Business logic
# --------------------------------------------------------------------------
def duration_discount(days: int) -> float:
    """Longer cover = small discount."""
    if days >= 30:
        return 0.15
    if days >= 14:
        return 0.10
    if days >= 7:
        return 0.05
    return 0.0


def calculate_premium(category: str, item_value: float, days: int, risk_multiplier: float) -> dict:
    """Daily premium = item value x category rate x risk multiplier."""
    rate = CATEGORIES[category]["rate"]
    daily = item_value * rate * risk_multiplier
    subtotal = daily * days
    discount_pct = duration_discount(days)
    total = max(subtotal * (1 - discount_pct), MIN_PREMIUM)
    return {
        "daily": round(daily, 2),
        "subtotal": round(subtotal, 2),
        "discount_pct": discount_pct,
        "total": round(total, 2),
    }


def coverage_window(policy: dict) -> tuple[datetime, datetime]:
    """Cover begins at the later of (start date 00:00) or (purchase time + waiting period)."""
    start_day = datetime.combine(date.fromisoformat(policy["start_date"]), time.min)
    purchased = datetime.fromisoformat(policy["purchased_at"])
    effective_start = max(start_day, purchased + timedelta(hours=policy["waiting_hours"]))
    end = datetime.combine(date.fromisoformat(policy["end_date"]), time.max)
    return effective_start, end


def policy_status(policy: dict, now: datetime | None = None) -> str:
    now = now or datetime.now()
    start, end = coverage_window(policy)
    if now < start:
        return "Upcoming"
    if now > end:
        return "Expired"
    return "Active"


def evaluate_claim(policy: dict, event_type: str, incident_dt: datetime, amount: float,
                   previous_payouts: float) -> tuple[str, float, str]:
    """Very simple rule engine. Returns (status, payout, reason)."""
    start, end = coverage_window(policy)
    covered_events = CATEGORIES[policy["category"]]["covered"]

    if event_type not in covered_events:
        return "Rejected", 0.0, f"'{event_type}' is not covered for {policy['category']} policies."

    if incident_dt < start:
        purchased = datetime.fromisoformat(policy["purchased_at"])
        if incident_dt < purchased + timedelta(hours=policy["waiting_hours"]):
            return "Rejected", 0.0, (
                f"Incident happened before the {policy['waiting_hours']}-hour waiting period ended."
            )
        return "Rejected", 0.0, "Incident happened before the cover started."

    if incident_dt > end:
        return "Rejected", 0.0, "Incident happened after the cover ended."

    if amount <= 0:
        return "Rejected", 0.0, "Claim amount must be greater than zero."

    remaining = policy["item_value"] - previous_payouts
    if remaining <= 0:
        return "Rejected", 0.0, "The insured value has already been fully paid out."

    claimable = min(amount, remaining)

    if claimable > policy["item_value"] * REVIEW_THRESHOLD:
        return "Under review", 0.0, (
            "Large claim: sent for manual review (in a real app an agent would check the photos)."
        )

    payout = round(claimable * (1 - DEDUCTIBLE_PCT), 2)
    return "Approved", payout, f"Approved after a {int(DEDUCTIBLE_PCT * 100)}% deductible."


# --------------------------------------------------------------------------
# Certificate generation
# --------------------------------------------------------------------------
def certificate_lines(policy: dict) -> list[str]:
    start, end = coverage_window(policy)
    return [
        f"Policy ID: {policy['id']}",
        f"Category: {policy['category']}",
        f"Insured item: {policy['item_name']}",
        f"Insured value: {CURRENCY} {policy['item_value']:,.0f}",
        f"Risk level: {policy['risk_label']}",
        f"Cover starts: {start:%d %b %Y, %H:%M}",
        f"Cover ends: {end:%d %b %Y, %H:%M}",
        f"Days covered: {policy['days']}",
        f"Total premium paid: {CURRENCY} {policy['total_premium']:,.2f}",
        f"Payment method: {policy['payment_method']}",
        f"Covered events: {', '.join(CATEGORIES[policy['category']]['covered'])}",
        f"Deductible: {int(DEDUCTIBLE_PCT * 100)}% of every approved claim",
        f"Issued: {datetime.fromisoformat(policy['purchased_at']):%d %b %Y, %H:%M}",
    ]


def build_certificate(policy: dict) -> tuple[bytes, str, str]:
    """Returns (file_bytes, filename, mime_type)."""
    lines = certificate_lines(policy)
    footer = "PROTOTYPE DEMO ONLY - this is not a real insurance policy."

    if PDF_AVAILABLE:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 12, "Pay-Per-Day Micro Insurance", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 10, "Digital Policy Certificate", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(6)
        for line in lines:
            pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)
        pdf.set_font("Helvetica", "I", 10)
        pdf.multi_cell(0, 6, footer, new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output()), f"certificate_{policy['id']}.pdf", "application/pdf"

    text = "PAY-PER-DAY MICRO INSURANCE - POLICY CERTIFICATE\n\n" + "\n".join(lines) + f"\n\n{footer}\n"
    return text.encode("utf-8"), f"certificate_{policy['id']}.txt", "text/plain"


# --------------------------------------------------------------------------
# UI pages
# --------------------------------------------------------------------------
def page_get_cover() -> None:
    st.header("Get Cover")
    st.write("Pick an item, choose your dates, and pay only for the days you need.")

    store = get_store()

    category = st.selectbox("What do you want to cover?", list(CATEGORIES.keys()))
    cfg = CATEGORIES[category]

    col1, col2 = st.columns(2)
    with col1:
        item_name = st.text_input("Item name / model", placeholder="e.g. Samsung S23, Honda CD70")
        item_value = st.number_input(
            f"Approximate value ({CURRENCY})",
            min_value=1000,
            max_value=cfg["max_value"],
            value=min(50_000, cfg["max_value"]),
            step=1000,
        )
    with col2:
        risk_label = st.selectbox("Where / how will you use it?", list(RISK_LEVELS.keys()))
        start_date = st.date_input("Cover starts", value=date.today(), min_value=date.today())
        end_date = st.date_input("Cover ends", value=date.today() + timedelta(days=2), min_value=start_date)

    days = (end_date - start_date).days + 1
    quote = calculate_premium(category, item_value, days, RISK_LEVELS[risk_label])

    st.subheader("Your quote")
    q1, q2, q3 = st.columns(3)
    q1.metric("Per day", f"{CURRENCY} {quote['daily']:,.2f}")
    q2.metric("Days", days)
    q3.metric("Total", f"{CURRENCY} {quote['total']:,.2f}")
    if quote["discount_pct"]:
        st.caption(f"Includes a {int(quote['discount_pct'] * 100)}% long-stay discount.")
    st.caption(
        f"Covered: {', '.join(cfg['covered'])}. "
        f"Not covered: {', '.join(NOT_COVERED_EVENTS)}. "
        f"Cover starts {WAITING_HOURS} hours after purchase at the earliest."
    )

    st.subheader("Checkout (simulated)")
    photo = st.file_uploader("Photo of the item (proof of condition)", type=["jpg", "jpeg", "png"])
    payment_method = st.radio("Payment method", PAYMENT_METHODS, horizontal=True)
    agree = st.checkbox("I understand this is a prototype and no real payment is made.")

    if st.button("Pay and activate cover", type="primary"):
        if not item_name.strip():
            st.error("Please enter the item name.")
        elif photo is None:
            st.error("Please upload a photo of the item.")
        elif not agree:
            st.error("Please tick the confirmation box.")
        else:
            policy = {
                "id": "POL-" + uuid.uuid4().hex[:8].upper(),
                "category": category,
                "item_name": item_name.strip(),
                "item_value": float(item_value),
                "risk_label": risk_label,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": days,
                "daily_premium": quote["daily"],
                "discount_pct": quote["discount_pct"],
                "total_premium": quote["total"],
                "payment_method": payment_method,
                "purchased_at": datetime.now().isoformat(timespec="seconds"),
                "waiting_hours": WAITING_HOURS,
                "photo_name": photo.name,
            }
            store["policies"].append(policy)
            save_store(store)
            st.session_state.last_policy_id = policy["id"]
            st.rerun()

    last_id = st.session_state.get("last_policy_id")
    if last_id:
        policy = next((p for p in store["policies"] if p["id"] == last_id), None)
        if policy:
            st.success(f"Payment received (simulated). Policy {policy['id']} is ready.")
            data, filename, mime = build_certificate(policy)
            st.download_button("Download certificate", data, file_name=filename, mime=mime)


def page_my_policies() -> None:
    st.header("My Policies")
    store = get_store()
    if not store["policies"]:
        st.info("No policies yet. Go to 'Get Cover' to buy your first one.")
        return

    for policy in reversed(store["policies"]):
        status = policy_status(policy)
        start, end = coverage_window(policy)
        icon = {"Active": "🟢", "Upcoming": "🟡", "Expired": "⚪"}[status]
        with st.expander(f"{icon} {policy['item_name']} ({policy['category']}) - {status}"):
            st.write(f"**Policy ID:** {policy['id']}")
            st.write(f"**Insured value:** {CURRENCY} {policy['item_value']:,.0f}")
            st.write(f"**Cover:** {start:%d %b %Y %H:%M} to {end:%d %b %Y %H:%M}")
            st.write(f"**Premium paid:** {CURRENCY} {policy['total_premium']:,.2f}")

            if status in ("Active", "Upcoming"):
                extra_days = st.number_input(
                    "Extend by (days)", min_value=1, max_value=30, value=1, key=f"ext_{policy['id']}"
                )
                new_end = date.fromisoformat(policy["end_date"]) + timedelta(days=int(extra_days))
                new_days = policy["days"] + int(extra_days)
                extra_cost = calculate_premium(
                    policy["category"], policy["item_value"], int(extra_days),
                    RISK_LEVELS[policy["risk_label"]],
                )["total"]
                st.caption(f"Extension cost: {CURRENCY} {extra_cost:,.2f} (new end date {new_end:%d %b %Y})")
                if st.button("Extend cover", key=f"extbtn_{policy['id']}"):
                    policy["end_date"] = new_end.isoformat()
                    policy["days"] = new_days
                    policy["total_premium"] = round(policy["total_premium"] + extra_cost, 2)
                    save_store(store)
                    st.rerun()

            data, filename, mime = build_certificate(policy)
            st.download_button(
                "Download certificate", data, file_name=filename, mime=mime, key=f"dl_{policy['id']}"
            )


def page_file_claim() -> None:
    st.header("File a Claim")
    store = get_store()
    if not store["policies"]:
        st.info("You need a policy before you can file a claim.")
        return

    options = {
        f"{p['id']} - {p['item_name']} ({policy_status(p)})": p for p in store["policies"]
    }
    policy = options[st.selectbox("Choose the policy", list(options.keys()))]

    events = CATEGORIES[policy["category"]]["covered"] + NOT_COVERED_EVENTS
    event_type = st.selectbox("What happened?", events)

    col1, col2 = st.columns(2)
    incident_date = col1.date_input("Incident date", value=date.today())
    incident_time = col2.time_input("Incident time", value=datetime.now().time().replace(microsecond=0))
    description = st.text_area("Describe what happened")
    amount = st.number_input(
        f"Claim amount ({CURRENCY})", min_value=0, max_value=int(policy["item_value"]), value=0, step=500
    )
    photos = st.file_uploader(
        "Photos / evidence", type=["jpg", "jpeg", "png"], accept_multiple_files=True
    )

    if st.button("Submit claim", type="primary"):
        if not description.strip():
            st.error("Please describe what happened.")
            return
        if not photos:
            st.error("Please upload at least one photo.")
            return

        incident_dt = datetime.combine(incident_date, incident_time)
        previous = sum(
            c["payout"] for c in store["claims"]
            if c["policy_id"] == policy["id"] and c["status"] == "Approved"
        )
        status, payout, reason = evaluate_claim(policy, event_type, incident_dt, float(amount), previous)

        claim = {
            "id": "CLM-" + uuid.uuid4().hex[:8].upper(),
            "policy_id": policy["id"],
            "item_name": policy["item_name"],
            "event_type": event_type,
            "incident_at": incident_dt.isoformat(timespec="minutes"),
            "description": description.strip(),
            "amount": float(amount),
            "photos": [p.name for p in photos],
            "status": status,
            "payout": payout,
            "reason": reason,
            "filed_at": datetime.now().isoformat(timespec="seconds"),
        }
        store["claims"].append(claim)
        save_store(store)

        if status == "Approved":
            st.success(f"Claim {claim['id']} approved. Payout: {CURRENCY} {payout:,.2f} (simulated). {reason}")
        elif status == "Under review":
            st.warning(f"Claim {claim['id']} is under review. {reason}")
        else:
            st.error(f"Claim {claim['id']} rejected. {reason}")


def page_claims() -> None:
    st.header("Claim Status")
    store = get_store()
    if not store["claims"]:
        st.info("No claims filed yet.")
        return

    for claim in reversed(store["claims"]):
        icon = {"Approved": "✅", "Rejected": "❌", "Under review": "⏳"}.get(claim["status"], "•")
        with st.expander(f"{icon} {claim['id']} - {claim['item_name']} - {claim['status']}"):
            st.write(f"**Event:** {claim['event_type']}")
            st.write(f"**When:** {claim['incident_at'].replace('T', ' ')}")
            st.write(f"**Claimed:** {CURRENCY} {claim['amount']:,.0f}")
            st.write(f"**Payout:** {CURRENCY} {claim['payout']:,.2f}")
            st.write(f"**Decision note:** {claim['reason']}")
            st.write(f"**Evidence files:** {', '.join(claim['photos'])}")


def page_about() -> None:
    st.header("How it works")
    st.markdown(
        f"""
**Pay-Per-Day Micro Insurance** lets you protect an item only for the days you need it.

**Pricing:** `daily premium = item value x category rate x risk multiplier`.
Covers of 7, 14 and 30+ days get a 5%, 10% and 15% discount. Minimum premium is {CURRENCY} {MIN_PREMIUM}.

**Anti-fraud rules (demo):**
- A {WAITING_HOURS}-hour waiting period after purchase.
- A photo of the item is required at purchase.
- A {int(DEDUCTIBLE_PCT * 100)}% deductible on approved claims.
- Claims above {int(REVIEW_THRESHOLD * 100)}% of the item value go to manual review.

**Prototype limits:** payments are simulated, claim decisions are rule-based, and
uploaded photos are not stored (only file names). A real launch needs a licensed insurer
or broker partner, real payment integration, and proper identity checks.
"""
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="Pay-Per-Day Micro Insurance", page_icon="🛡️", layout="centered")
    st.title("🛡️ Pay-Per-Day Micro Insurance")
    st.caption("Prototype - simulated payments and claims")

    pages = {
        "Get Cover": page_get_cover,
        "My Policies": page_my_policies,
        "File a Claim": page_file_claim,
        "Claim Status": page_claims,
        "How it works": page_about,
    }
    choice = st.sidebar.radio("Menu", list(pages.keys()))

    st.sidebar.divider()
    if st.sidebar.button("Reset demo data"):
        st.session_state.store = {"policies": [], "claims": []}
        st.session_state.pop("last_policy_id", None)
        save_store(st.session_state.store)
        st.rerun()

    pages[choice]()


if __name__ == "__main__":
    main()
