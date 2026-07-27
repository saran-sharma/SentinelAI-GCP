#!/usr/bin/env python3
"""Render the SentinelAI architecture diagram as SVG.

Diagram-as-code, for the same reason the infrastructure is: a binary exported
from a drawing tool cannot be reviewed in a pull request, and drifts from the
system the moment either changes. This emits a 1920x1080 (16:9) SVG that
renders natively in the GitHub README and scales losslessly for slides or
LinkedIn.

The icons are drawn here in Google Cloud's visual language — flat geometry in
the Google brand palette — rather than being the official Google Cloud icon
set, which is a licensed asset pack this repository does not vendor. Swap in
the official SVGs from cloud.google.com/icons if you want exact fidelity.

    python3 scripts/render_architecture.py            # -> docs/images/architecture.svg
"""

from __future__ import annotations

import pathlib

# --- Google Cloud palette ---------------------------------------------------

BLUE = "#4285F4"
RED = "#EA4335"
YELLOW = "#F9AB00"
GREEN = "#34A853"
INK = "#202124"
MUTED = "#5F6368"
LINE = "#DADCE0"
SURFACE = "#F8F9FA"
TINT = "#E8F0FE"

W, H = 1920, 1080
FONT = "'Google Sans','Roboto','Segoe UI',Helvetica,Arial,sans-serif"

out: list[str] = []


def add(markup: str) -> None:
    out.append(markup)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(
    x: float,
    y: float,
    body: str,
    size: int = 15,
    fill: str = INK,
    weight: int = 400,
    anchor: str = "start",
    spacing: str = "0",
) -> None:
    add(
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}" letter-spacing="{spacing}">{esc(body)}</text>'
    )


def group(x: float, y: float, w: float, h: float, label: str, accent: str) -> None:
    """A layer container: soft surface, coloured top rule, small-caps label."""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{SURFACE}" stroke="{LINE}"/>')
    add(f'<rect x="{x}" y="{y}" width="{w}" height="4" rx="2" fill="{accent}"/>')
    text(x + 18, y + 32, label.upper(), size=12, fill=MUTED, weight=500, spacing="1.4")


def card(x: float, y: float, w: float, h: float, title: str, sub: str = "", icon: str = "") -> None:
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#FFFFFF" stroke="{LINE}"/>')
    if icon:
        draw_icon(icon, x + 16, y + (h - 34) / 2 if not sub else y + 18)
    tx = x + 62 if icon else x + 16
    if sub:
        text(tx, y + 34, title, size=15, weight=500)
        for i, part in enumerate(sub.split("|")):
            text(tx, y + 56 + i * 19, part.strip(), size=12.5, fill=MUTED)
    else:
        text(tx, y + h / 2 + 5, title, size=15, weight=500)


def chip(x: float, y: float, w: float, h: float, label: str, accent: str = BLUE) -> None:
    add(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{TINT}" '
        f'stroke="{accent}" stroke-opacity="0.35"/>'
    )
    lines = label.split("|")
    start = y + h / 2 + 5 - (len(lines) - 1) * 8
    for i, line in enumerate(lines):
        text(x + w / 2, start + i * 16, line.strip(), size=12.5, fill="#174EA6", weight=500, anchor="middle")


def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "", dashed: bool = False) -> None:
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    add(
        f'<path d="M {x1} {y1} L {x2} {y2}" stroke="{MUTED}" stroke-width="2" fill="none" '
        f'marker-end="url(#arrow)"{dash}/>'
    )
    if label:
        # No pill: the gap between layer groups is 32px and a boxed label
        # overhangs both borders. Small text above the line reads cleanly.
        text((x1 + x2) / 2, (y1 + y2) / 2 - 9, label, size=10.5, fill=MUTED, anchor="middle", weight=500)


# --- icons (34x34 boxes, flat geometry in the Google palette) ---------------


def draw_icon(kind: str, x: float, y: float) -> None:
    g = f'<g transform="translate({x},{y})">'
    add(g)
    if kind == "logging":
        add(f'<rect x="4" y="2" width="26" height="30" rx="4" fill="{BLUE}"/>')
        for i, wd in enumerate((16, 12, 15)):
            add(f'<rect x="9" y="{9 + i * 7}" width="{wd}" height="3" rx="1.5" fill="#FFFFFF"/>')
    elif kind == "monitoring":
        add(f'<rect x="3" y="3" width="28" height="28" rx="6" fill="{RED}"/>')
        add(
            '<polyline points="9,23 14,16 19,20 25,10" stroke="#FFFFFF" stroke-width="3" fill="none" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    elif kind == "billing":
        add(f'<circle cx="17" cy="17" r="14" fill="{YELLOW}"/>')
        add('<text x="17" y="23" font-size="17" fill="#FFFFFF" text-anchor="middle" font-weight="700">$</text>')
    elif kind == "pubsub":
        add(f'<path d="M17 2 L30 9.5 L30 24.5 L17 32 L4 24.5 L4 9.5 Z" fill="{GREEN}"/>')
        add('<circle cx="12" cy="17" r="3" fill="#FFFFFF"/><circle cx="22" cy="11" r="3" fill="#FFFFFF"/>')
        add('<circle cx="22" cy="23" r="3" fill="#FFFFFF"/>')
        add('<path d="M14.5 15.5 L19.5 12.5 M14.5 18.5 L19.5 21.5" stroke="#FFFFFF" stroke-width="1.6"/>')
    elif kind == "run":
        add(f'<rect x="3" y="6" width="28" height="22" rx="5" fill="{BLUE}"/>')
        add('<path d="M13 12 L22 17 L13 22 Z" fill="#FFFFFF"/>')
    elif kind == "ai":
        add(f'<path d="M17 2 L21 13 L32 17 L21 21 L17 32 L13 21 L2 17 L13 13 Z" fill="{BLUE}"/>')
    elif kind == "firestore":
        add(f'<path d="M6 12 L17 5 L28 12 L17 19 Z" fill="{YELLOW}"/>')
        add(f'<path d="M6 20 L17 13 L28 20 L17 27 Z" fill="{YELLOW}" opacity="0.72"/>')
        add(f'<path d="M6 27 L17 20 L28 27 L17 34 Z" fill="{YELLOW}" opacity="0.45"/>')
    elif kind == "storage":
        add(f'<path d="M4 8 L30 8 L26 30 L8 30 Z" fill="{GREEN}"/>')
        add('<rect x="9" y="14" width="16" height="3" rx="1.5" fill="#FFFFFF"/>')
    elif kind == "slack":
        for dx, dy, w_, h_ in ((10, 4, 4, 26), (20, 4, 4, 26)):
            add(f'<rect x="{dx}" y="{dy}" width="{w_}" height="{h_}" rx="2" fill="{RED}"/>')
        for dx, dy, w_, h_ in ((4, 10, 26, 4), (4, 20, 26, 4)):
            add(f'<rect x="{dx}" y="{dy}" width="{w_}" height="{h_}" rx="2" fill="{RED}" opacity="0.75"/>')
    elif kind == "scheduler":
        add(f'<circle cx="17" cy="17" r="14" fill="{BLUE}"/>')
        add('<path d="M17 9 L17 17 L23 20" stroke="#FFFFFF" stroke-width="3" fill="none" stroke-linecap="round"/>')
    elif kind == "secret":
        add(f'<rect x="7" y="15" width="20" height="16" rx="4" fill="{RED}"/>')
        add(f'<path d="M11 15 V11 a6 6 0 0 1 12 0 V15" stroke="{RED}" stroke-width="3.4" fill="none"/>')
        add('<circle cx="17" cy="23" r="2.6" fill="#FFFFFF"/>')
    elif kind == "registry":
        add(f'<path d="M17 3 L30 10 V24 L17 31 L4 24 V10 Z" fill="{BLUE}"/>')
        add('<path d="M4 10 L17 17 L30 10 M17 17 V31" stroke="#FFFFFF" stroke-width="1.8" fill="none"/>')
    elif kind == "iam":
        add(f'<path d="M17 3 L29 8 V17 c0 8 -6 12 -12 14 C11 29 5 25 5 17 V8 Z" fill="{GREEN}"/>')
        add('<circle cx="17" cy="15" r="3.6" fill="#FFFFFF"/>')
        add('<path d="M11 24 c1.5 -4 10.5 -4 12 0 Z" fill="#FFFFFF"/>')
    elif kind == "trace":
        add(f'<rect x="3" y="3" width="28" height="28" rx="6" fill="{YELLOW}"/>')
        add(
            '<path d="M7 17 h5 l3 -7 l4 14 l3 -7 h5" stroke="#FFFFFF" stroke-width="2.6" fill="none" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    elif kind == "dashboard":
        add(f'<rect x="3" y="3" width="28" height="28" rx="6" fill="{BLUE}"/>')
        for bx, bh in ((9, 8), (15, 14), (21, 11)):
            add(f'<rect x="{bx}" y="{25 - bh}" width="4" height="{bh}" rx="2" fill="#FFFFFF"/>')
    elif kind == "terraform":
        add(f'<path d="M4 9 L13 4 V15 L4 20 Z" fill="{BLUE}"/>')
        add(f'<path d="M15 15 L24 10 V21 L15 26 Z" fill="{BLUE}" opacity="0.62"/>')
        add(f'<path d="M15 3 L24 8 V19 L15 14 Z" fill="{BLUE}" opacity="0.85"/>')
    elif kind == "github":
        add(f'<circle cx="17" cy="17" r="14" fill="{INK}"/>')
        add('<circle cx="12" cy="12" r="2.6" fill="#FFFFFF"/><circle cx="12" cy="23" r="2.6" fill="#FFFFFF"/>')
        add('<circle cx="22" cy="12" r="2.6" fill="#FFFFFF"/>')
        add('<path d="M12 14.6 V20.4 M12 17 h6 a4 4 0 0 0 4 -4" stroke="#FFFFFF" stroke-width="2" fill="none"/>')
    elif kind == "actions":
        add(f'<circle cx="17" cy="17" r="14" fill="{BLUE}"/>')
        add('<circle cx="17" cy="17" r="6" fill="none" stroke="#FFFFFF" stroke-width="2.6"/>')
        add('<path d="M17 8 v4 M17 22 v4" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round"/>')
    elif kind == "podman":
        add(f'<rect x="4" y="12" width="26" height="18" rx="3" fill="{GREEN}"/>')
        for i in range(3):
            add(f'<rect x="{8 + i * 7}" y="6" width="5" height="5" rx="1" fill="{GREEN}" opacity="0.7"/>')
        add('<rect x="9" y="18" width="16" height="3" rx="1.5" fill="#FFFFFF"/>')
    add("</g>")


# --- canvas -----------------------------------------------------------------

add(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
    f'font-family="{FONT}" role="img" aria-label="SentinelAI architecture on Google Cloud">'
)
add(
    '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTED}"/></marker></defs>'
)
add(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

# Header
for i, colour in enumerate((BLUE, RED, YELLOW, GREEN)):
    add(f'<rect x="{i * W / 4}" y="0" width="{W / 4}" height="6" fill="{colour}"/>')
text(48, 62, "SentinelAI — AI-Powered Incident Triage Platform on Google Cloud", size=34, weight=500)
text(
    48,
    92,
    "Event-driven AIOps  ·  deterministic deduplication before AI inference  ·  severity-gated paging",
    size=15.5,
    fill=MUTED,
)

# --- runtime layers ---------------------------------------------------------

TOP, GH = 130, 440
cols = [(48, 250), (330, 190), (552, 470), (1054, 250), (1336, 250), (1618, 250)]
labels = [
    ("Event sources", RED),
    ("Messaging", GREEN),
    ("Compute · Cloud Run", BLUE),
    ("AI", BLUE),
    ("State & artifacts", YELLOW),
    ("Delivery", RED),
]
for (cx, cw), (lab, accent) in zip(cols, labels, strict=True):
    group(cx, TOP, cw, GH, lab, accent)

# 1 — event sources
for i, (title, sub, icon) in enumerate(
    [
        ("Cloud Logging", "severity >= ERROR|log sink", "logging"),
        ("Cloud Monitoring", "alert policies", "monitoring"),
        ("Budget Alerts", "spend thresholds", "billing"),
        ("Pub/Sub events", "external producers", "pubsub"),
    ]
):
    card(66, 178 + i * 96, 214, 84, title, sub, icon)

# 2 — messaging
card(348, 258, 154, 96, "Pub/Sub", "sentinelai-events|push + OIDC", "pubsub")
card(348, 386, 154, 84, "Dead-letter", "5 attempts", "pubsub")
arrow(425, 358, 425, 382, dashed=True)

# 3 — Cloud Run
card(570, 178, 434, 74, "Cloud Run · SentinelAI Triage", "FastAPI · private · scale-to-zero", "run")
add(f'<rect x="570" y="266" width="434" height="286" rx="10" fill="#FFFFFF" stroke="{LINE}"/>')
text(586, 292, "REQUEST PIPELINE", size=11, fill=MUTED, weight=500, spacing="1.2")
pipeline = [
    "Event|normalization",
    "Finger-|printing",
    "Dedup-|lication",
    "AI triage|engine",
    "Severity|classification",
    "Suppression|logic",
]
for i, label in enumerate(pipeline):
    chip(586 + (i % 3) * 138, 306 + (i // 3) * 96, 126, 80, label)
text(586, 534, "Gemini is called only past the suppression gate", size=12, fill=MUTED)

# 4 — AI
card(1072, 178, 214, 96, "Vertex AI", "Gemini 2.5 Flash", "ai")
for i, label in enumerate(["Root cause analysis", "Incident summary", "Recommended actions"]):
    chip(1072, 296 + i * 56, 214, 46, label)
add(f'<rect x="1072" y="472" width="214" height="62" rx="8" fill="#FFFFFF" stroke="{LINE}"/>')
text(1082, 496, "Heuristic fallback", size=13, weight=500)
text(1082, 516, "triage survives an AI outage", size=11.5, fill=MUTED)

# 5 — state
card(1354, 178, 214, 108, "Firestore", "incident history|doc id = fingerprint", "firestore")
card(1354, 310, 214, 108, "Cloud Storage", "digest reports|incident artifacts", "storage")
add(f'<rect x="1354" y="442" width="214" height="92" rx="8" fill="#FFFFFF" stroke="{LINE}"/>')
text(1364, 466, "Cloud Monitoring", size=13, weight=500)
text(1364, 486, "custom + log-based metrics", size=11.5, fill=MUTED)
text(1364, 505, "5 alert policies · dashboard", size=11.5, fill=MUTED)

# 6 — delivery
card(1636, 178, 214, 96, "Slack", "SEV1–SEV3 only", "slack")
add(f'<rect x="1636" y="298" width="214" height="102" rx="8" fill="{TINT}" stroke="{BLUE}" stroke-opacity="0.35"/>')
text(1743, 326, "SEV4 is recorded,", size=12.5, fill="#174EA6", anchor="middle", weight=500)
text(1743, 344, "counted and queryable —", size=12.5, fill="#174EA6", anchor="middle", weight=500)
text(1743, 362, "and never pages anyone.", size=12.5, fill="#174EA6", anchor="middle", weight=500)
card(1636, 424, 214, 108, "Cloud Scheduler", "daily AI digest|OIDC invocation", "scheduler")

# flow arrows between layers
arrow(298, 356, 344, 356, "publish")
arrow(506, 356, 566, 356, "push")
arrow(1008, 356, 1068, 356, "analyse")
arrow(1290, 356, 1350, 356, "persist")
arrow(1572, 356, 1632, 356, "notify")
# scheduler feeds the service back
add(
    f'<path d="M 1743 532 L 1743 588 L 787 588 L 787 552" stroke="{MUTED}" stroke-width="2" fill="none" '
    f'stroke-dasharray="6 5" marker-end="url(#arrow)"/>'
)
add('<rect x="1128" y="578" width="244" height="20" rx="10" fill="#FFFFFF"/>')
text(1250, 592, "scheduled digest → /jobs/digest", size=11.5, fill=MUTED, anchor="middle")

# --- supporting services ----------------------------------------------------

group(48, 606, 1820, 118, "Supporting services", BLUE)
support = [
    ("Secret Manager", "secret"),
    ("Artifact Registry", "registry"),
    ("IAM service accounts", "iam"),
    ("Cloud Trace", "trace"),
    ("Cloud Logging", "logging"),
    ("Monitoring dashboard", "dashboard"),
]
for i, (name, icon) in enumerate(support):
    card(66 + i * 300, 648, 286, 60, name, "", icon)

# --- infrastructure + devops -----------------------------------------------

group(48, 746, 890, 246, "Infrastructure as code", YELLOW)
draw_icon("terraform", 70, 790)
text(116, 812, "Terraform · 11 modules · GCS remote state", size=16, weight=500)
managed = [
    "Cloud Run",
    "Pub/Sub",
    "Firestore",
    "Monitoring",
    "IAM",
    "Cloud Scheduler",
    "Artifact Registry",
    "Secret Manager",
]
for i, name in enumerate(managed):
    chip(70 + (i % 4) * 212, 846 + (i // 4) * 62, 198, 50, name, YELLOW)
text(70, 976, "Everything reproducible · nothing clicked", size=12, fill=MUTED)

group(988, 746, 880, 246, "DevOps pipeline", GREEN)
stages = [
    ("GitHub", "github"),
    ("GitHub Actions", "actions"),
    ("Podman build", "podman"),
    ("Artifact Registry", "registry"),
    ("Terraform deploy", "terraform"),
    ("Cloud Run", "run"),
]
for i, (name, icon) in enumerate(stages):
    px, py = 1010 + (i % 3) * 286, 800 + (i // 3) * 96
    add(f'<rect x="{px}" y="{py}" width="252" height="64" rx="10" fill="#FFFFFF" stroke="{LINE}"/>')
    draw_icon(icon, px + 14, py + 15)
    text(px + 60, py + 38, name, size=14, weight=500)
    if i % 3 != 2:
        arrow(px + 252, py + 32, px + 282, py + 32)
add(
    f'<path d="M 1708 864 L 1708 880 L 1136 880 L 1136 892" stroke="{MUTED}" '
    f'stroke-width="2" fill="none" marker-end="url(#arrow)"/>'
)
text(1010, 976, "Keyless deploys via Workload Identity Federation — no service account keys", size=12, fill=MUTED)

# --- footer -----------------------------------------------------------------

add(f'<line x1="48" y1="1012" x2="1872" y2="1012" stroke="{LINE}"/>')
text(
    48,
    1042,
    "Production-ready AI Incident Management Platform built using Google Cloud, FastAPI, "
    "Terraform, GitHub Actions and Gemini AI.",
    size=14.5,
    fill=MUTED,
)
text(1872, 1042, "github.com/saran-sharma/SentinelAI-GCP", size=14.5, fill=MUTED, anchor="end")

add("</svg>")

target = pathlib.Path(__file__).resolve().parent.parent / "docs" / "images" / "architecture.svg"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("\n".join(out), encoding="utf-8")
print(f"wrote {target} ({target.stat().st_size:,} bytes)")
