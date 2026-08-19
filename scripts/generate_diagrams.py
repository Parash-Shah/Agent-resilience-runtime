from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"
FONT = ImageFont.load_default(size=20)
SMALL = ImageFont.load_default(size=16)
BG = "#0b1220"
BOX = "#17233a"
LINE = "#60a5fa"
TEXT = "#f8fafc"
MUTED = "#b8c4d8"
ACCENT = "#34d399"


def box(draw, xy, title, subtitle="", color=BOX):
    draw.rounded_rectangle(xy, radius=12, fill=color, outline=LINE, width=2)
    x1, y1, x2, y2 = xy
    draw.text(((x1 + x2) / 2, y1 + 18), title, fill=TEXT, font=FONT, anchor="ma")
    if subtitle:
        draw.multiline_text(((x1 + x2) / 2, y1 + 50), subtitle, fill=MUTED, font=SMALL, anchor="ma", align="center", spacing=5)


def arrow(draw, start, end, label=""):
    draw.line([start, end], fill=LINE, width=3)
    x, y = end
    draw.polygon([(x, y), (x - 10, y - 6), (x - 10, y + 6)], fill=LINE)
    if label:
        draw.text(((start[0] + end[0]) / 2, start[1] - 10), label, fill=MUTED, font=SMALL, anchor="ms")


def architecture():
    image = Image.new("RGB", (1500, 900), BG)
    draw = ImageDraw.Draw(image)
    draw.text((750, 32), "AgentResilience - durable incident execution", fill=TEXT, font=FONT, anchor="ma")
    boxes = {
        "gateway": (60, 120, 310, 230), "orchestrator": (430, 120, 730, 230),
        "tools": (850, 120, 1130, 230), "systems": (1250, 120, 1440, 230),
        "checkpoint": (120, 390, 390, 510), "queue": (470, 390, 740, 510),
        "policy": (820, 390, 1090, 510), "approval": (1170, 390, 1430, 510),
        "observe": (470, 680, 1030, 800),
    }
    box(draw, boxes["gateway"], "FastAPI gateway", "goals, status, events")
    box(draw, boxes["orchestrator"], "Agents SDK orchestrator", "typed decisions + specialists")
    box(draw, boxes["tools"], "Tool gateway", "validate | authorize | dedupe")
    box(draw, boxes["systems"], "Systems", "logs | metrics | AWS")
    box(draw, boxes["checkpoint"], "Checkpoint store", "optimistic versions\nlast known good state")
    box(draw, boxes["queue"], "Durable queue", "leases | retry | DLQ")
    box(draw, boxes["policy"], "Permission policy", "LOW | MEDIUM | HIGH | BLOCKED")
    box(draw, boxes["approval"], "Human approval", "authenticated decision gate")
    box(draw, boxes["observe"], "Observability", "OpenAI traces | OpenTelemetry | Prometheus | Grafana", color="#12302d")
    arrow(draw, (310, 175), (430, 175), "goal")
    arrow(draw, (730, 175), (850, 175), "tool request")
    arrow(draw, (1130, 175), (1250, 175), "bounded call")
    arrow(draw, (255, 390), (255, 235), "resume")
    arrow(draw, (605, 390), (605, 235), "deliver")
    arrow(draw, (955, 390), (955, 235), "authorize")
    arrow(draw, (1300, 390), (1300, 235), "approve")
    draw.line([(255, 510), (255, 620), (750, 620), (750, 680)], fill=ACCENT, width=3)
    draw.line([(605, 510), (605, 620)], fill=ACCENT, width=3)
    draw.line([(955, 510), (955, 620)], fill=ACCENT, width=3)
    draw.line([(1300, 510), (1300, 620), (750, 620)], fill=ACCENT, width=3)
    image.save(OUT / "agent-interactions.png")


def sequence():
    image = Image.new("RGB", (1500, 900), BG)
    draw = ImageDraw.Draw(image)
    draw.text((750, 32), "Crash recovery sequence", fill=TEXT, font=FONT, anchor="ma")
    lanes = [(140, "Gateway"), (440, "Queue / worker"), (750, "Orchestrator"), (1060, "Checkpoint"), (1360, "Tool gateway")]
    for x, label in lanes:
        draw.text((x, 95), label, fill=TEXT, font=FONT, anchor="ma")
        draw.line([(x, 125), (x, 840)], fill="#334155", width=2)
    events = [
        (170, 140, 440, "enqueue goal"), (250, 440, 750, "decide: inspect metrics"),
        (330, 750, 1060, "checkpoint intent"), (410, 750, 1360, "execute (idempotency key)"),
        (490, 1360, 750, "tool result"), (570, 750, 1060, "checkpoint result OK"),
        (650, 440, 750, "next delivery"), (720, 750, 750, "PROCESS CRASH"),
        (790, 440, 1060, "lease expires; reload checkpoint"),
        (840, 1060, 750, "resume next incomplete step"),
    ]
    for y, left, right, label in events:
        if left == right:
            draw.rounded_rectangle((left - 100, y - 22, right + 100, y + 22), 8, fill="#7f1d1d")
            draw.text((left, y), label, fill=TEXT, font=SMALL, anchor="mm")
        else:
            arrow(draw, (left, y), (right, y), label)
    image.save(OUT / "agent-sequence.png")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    architecture()
    sequence()
