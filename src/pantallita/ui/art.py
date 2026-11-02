"""Everything Pantallita looks like, drawn with QPainter: an original flat style.

Few colors, simple shapes, soft edges. No image, font or icon files are used.
The mascot is a tiny inventor shaped like a light bulb (an idea with legs): a glass head
with simple eyes that follow the mouse, a teal screw-cap body, thin limbs, and a warm glow
that only shows while he works.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen

INK = QColor("#24303d")
MUSTARD = QColor("#f2b134")
MUSTARD_DARK = QColor("#d89a22")
TEAL = QColor("#2a9d8f")
TEAL_LIGHT = QColor("#8fd3c7")
CREAM = QColor("#fff6e5")
CORAL = QColor("#e76f51")
PAPER = QColor("#fffdf8")
SHADOW = QColor(36, 48, 61, 60)


def pen(color: QColor, width: float = 2.0) -> QPen:
    p = QPen(color, width)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def font(size: float, bold: bool = False) -> QFont:
    f = QFont("Segoe UI")
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


# --- mascot --------------------------------------------------------------------------------

def draw_mascot(p: QPainter, cx: float, base: float, s: float, state: str, t: float,
                look: tuple[float, float] = (0.0, 0.0), facing: int = 1, blink: bool = False) -> None:
    """Draw the inventor standing with his feet on y=`base`, centered on x=`cx`, scale `s`
    (s=1 is about 64 px tall). `look` is where the pupils point, each in [-1, 1].

    He is a small light bulb (an idea with legs): a glass head, a screw-cap body, thin limbs.
    The glow around the glass tells his mood: off when idle, warm when he works."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    bob = 0.0
    lean = 0.0
    if state == "walk":
        bob = abs(math.sin(t * 9)) * 3 * s
    elif state in ("build", "catch"):
        bob = abs(math.sin(t * 14)) * 2 * s
    elif state == "confused":
        lean = math.sin(t * 2) * 5
    elif state == "yawn":
        lean = math.sin(t * 1.3) * 2

    p.setPen(Qt.NoPen)
    p.setBrush(SHADOW)
    p.drawEllipse(QPointF(cx, base - 1 * s), 16 * s, 3.5 * s)

    # legs
    step = math.sin(t * 9) * 5 * s if state == "walk" else 0
    p.setPen(pen(INK, 3.2 * s))
    p.drawLine(QPointF(cx - 5 * s, base - 13 * s - bob), QPointF(cx - 6 * s + step, base - 2 * s))
    p.drawLine(QPointF(cx + 5 * s, base - 13 * s - bob), QPointF(cx + 6 * s - step, base - 2 * s))
    p.setPen(Qt.NoPen)
    p.setBrush(INK)
    p.drawEllipse(QPointF(cx - 6 * s + step - 1.5 * s, base - 2 * s), 3.2 * s, 2 * s)
    p.drawEllipse(QPointF(cx + 6 * s - step + 1.5 * s, base - 2 * s), 3.2 * s, 2 * s)

    p.translate(cx, base - 13 * s - bob)
    p.rotate(lean)

    # glow (only while he works or has an idea)
    glow = {"build": 0.55, "verify": 0.45, "catch": 0.5, "sign": 0.4}.get(state, 0.0)
    if glow:
        pulse = 0.75 + 0.25 * math.sin(t * 6)
        p.setPen(Qt.NoPen)
        for k, a in ((1.55, 40), (1.3, 70)):
            p.setBrush(QColor(MUSTARD.red(), MUSTARD.green(), MUSTARD.blue(), int(a * glow * pulse)))
            p.drawEllipse(QPointF(0, -38 * s), 22 * s * k, 22 * s * k)

    # screw cap (the body): teal with two threads
    cap = QRectF(-11 * s, -15 * s, 22 * s, 15 * s)
    p.setPen(pen(INK, 2 * s))
    p.setBrush(TEAL)
    p.drawRoundedRect(cap, 4 * s, 4 * s)
    p.setPen(pen(INK, 1.6 * s))
    for y in (-10 * s, -5 * s):
        p.drawLine(QPointF(-10 * s, y), QPointF(10 * s, y + 1.5 * s))
    p.setPen(Qt.NoPen)
    p.setBrush(INK)
    p.drawRoundedRect(QRectF(-4 * s, -1.5 * s, 8 * s, 3 * s), 1.5 * s, 1.5 * s)

    # arms, from the cap
    p.setPen(pen(INK, 3 * s))
    if state == "catch":
        p.drawLine(QPointF(-10 * s, -10 * s), QPointF(-24 * s, -34 * s))
        p.drawLine(QPointF(10 * s, -10 * s), QPointF(24 * s, -34 * s))
    elif state == "build":
        swing = math.sin(t * 14) * 9 * s
        p.drawLine(QPointF(-10 * s, -10 * s), QPointF(-20 * s, -2 * s))
        p.drawLine(QPointF(10 * s, -10 * s), QPointF(22 * s, -18 * s + swing))
        p.setPen(pen(INK, 1.8 * s))  # a tiny wrench
        p.drawLine(QPointF(22 * s, -18 * s + swing), QPointF(28 * s, -24 * s + swing))
        p.setBrush(CORAL)
        p.drawEllipse(QPointF(29 * s, -25 * s + swing), 3 * s, 3 * s)
    elif state == "confused":
        p.drawLine(QPointF(-10 * s, -10 * s), QPointF(-20 * s, 0))
        scratch = math.sin(t * 12) * 3 * s
        p.drawLine(QPointF(10 * s, -10 * s), QPointF(12 * s + scratch, -52 * s))
    elif state == "verify":
        p.drawLine(QPointF(-10 * s, -10 * s), QPointF(-20 * s, -2 * s))
        stamp = abs(math.sin(t * 6)) * 8 * s
        p.drawLine(QPointF(10 * s, -10 * s), QPointF(24 * s, -8 * s + stamp))
        p.setBrush(TEAL)
        p.setPen(pen(INK, 1.6 * s))
        p.drawRoundedRect(QRectF(20 * s, -6 * s + stamp, 9 * s, 6 * s), 1.5 * s, 1.5 * s)
    elif state == "sign":
        p.drawLine(QPointF(-10 * s, -10 * s), QPointF(-20 * s, -52 * s))
        p.drawLine(QPointF(10 * s, -10 * s), QPointF(20 * s, -52 * s))
    else:
        swing = math.sin(t * 9) * 4 * s if state == "walk" else 0
        p.drawLine(QPointF(-10 * s, -10 * s), QPointF(-19 * s, 0 + swing))
        p.drawLine(QPointF(10 * s, -10 * s), QPointF(19 * s, 0 - swing))

    # glass head: a circle that narrows into the cap
    head = QPainterPath()
    head.addEllipse(QPointF(0, -38 * s), 19 * s, 19 * s)
    neck = QPainterPath()
    neck.moveTo(-12 * s, -26 * s)
    neck.cubicTo(-10 * s, -20 * s, -9 * s, -17 * s, -9 * s, -14 * s)
    neck.lineTo(9 * s, -14 * s)
    neck.cubicTo(9 * s, -17 * s, 10 * s, -20 * s, 12 * s, -26 * s)
    neck.closeSubpath()
    p.setPen(pen(INK, 2.2 * s))
    p.setBrush(CREAM if not glow else QColor("#fff1c9"))
    p.drawPath(head.united(neck))
    # glass shine
    p.setPen(pen(QColor(255, 255, 255, 220), 2.4 * s))
    p.drawArc(QRectF(-14 * s, -52 * s, 12 * s, 14 * s), 100 * 16, 70 * 16)

    # eyes
    lx, ly = max(-1, min(1, look[0])), max(-1, min(1, look[1]))
    for ex in (-7 * s, 7 * s):
        center = QPointF(ex, -40 * s)
        if blink or (state == "yawn" and math.sin(t * 1.3) > 0.6):
            p.setPen(pen(INK, 2 * s))
            p.drawLine(QPointF(ex - 3.5 * s, -40 * s), QPointF(ex + 3.5 * s, -40 * s))
            continue
        p.setPen(pen(INK, 1.6 * s))
        p.setBrush(PAPER)
        p.drawEllipse(center, 5 * s, 5.6 * s)
        p.setPen(Qt.NoPen)
        p.setBrush(INK)
        p.drawEllipse(QPointF(ex + lx * 2 * s, -40 * s + ly * 2.2 * s), 2.6 * s, 2.6 * s)
    # eyebrows give the mood
    p.setPen(pen(INK, 1.8 * s))
    if state == "confused":
        p.drawLine(QPointF(-11 * s, -49 * s), QPointF(-4 * s, -47 * s))
        p.drawLine(QPointF(4 * s, -50 * s), QPointF(11 * s, -48 * s))

    # mouth
    p.setPen(pen(INK, 1.8 * s))
    p.setBrush(Qt.NoBrush)
    if state == "yawn" and math.sin(t * 1.3) > 0:
        p.setBrush(CORAL)
        p.drawEllipse(QPointF(0, -30 * s), 3 * s, 2.5 * s * (0.5 + math.sin(t * 1.3)))
    elif state == "confused":
        p.drawLine(QPointF(-3 * s, -30 * s), QPointF(3 * s, -31 * s))
    else:
        p.drawArc(QRectF(-5.5 * s, -36 * s, 11 * s, 9 * s), 200 * 16, 140 * 16)
    p.restore()

    if state == "confused":
        draw_sparks(p, QPointF(cx + 20 * s * facing, base - 72 * s), t, s * 0.7)


def draw_film(p: QPainter, x: float, y: float, t: float, s: float = 1.0) -> None:
    """His memory: a film strip unrolling from (x, y) to the side while he rewinds."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    length = (60 + 40 * math.sin(t * 2)) * s
    frame_w = 16 * s
    p.setPen(pen(INK, 1.8 * s))
    p.setBrush(INK)
    p.drawRoundedRect(QRectF(x, y, length, 20 * s), 3 * s, 3 * s)
    offset = (t * 40 * s) % frame_w
    p.setPen(Qt.NoPen)
    for i in range(int(length // frame_w) + 1):
        fx = x + i * frame_w - offset
        if x <= fx <= x + length - frame_w + 2:
            p.setBrush(TEAL_LIGHT if i % 3 else MUSTARD)
            p.drawRoundedRect(QRectF(fx + 3 * s, y + 5 * s, frame_w - 6 * s, 10 * s), 1.5 * s, 1.5 * s)
            p.setBrush(CREAM)
            p.drawRect(QRectF(fx + 5 * s, y + 1.5 * s, 3 * s, 2 * s))
            p.drawRect(QRectF(fx + 5 * s, y + 16.5 * s, 3 * s, 2 * s))
    p.setPen(pen(INK, 1.8 * s))
    p.setBrush(INK)
    p.drawEllipse(QPointF(x, y + 10 * s), 9 * s, 9 * s)  # the reel
    p.setBrush(CREAM)
    p.drawEllipse(QPointF(x, y + 10 * s), 3 * s, 3 * s)
    p.restore()


def draw_sparks(p: QPainter, at: QPointF, t: float, s: float = 1.0) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    for i in range(6):
        a = i * math.pi / 3 + t * 5
        r = (6 + 6 * ((t * 3 + i * 0.37) % 1)) * s
        p.setPen(pen(CORAL if i % 2 else MUSTARD, 2.2 * s))
        p.drawLine(QPointF(at.x() + math.cos(a) * r * 0.5, at.y() + math.sin(a) * r * 0.5),
                   QPointF(at.x() + math.cos(a) * r, at.y() + math.sin(a) * r))
    p.restore()


def draw_bubble(p: QPainter, anchor: QPointF, text: str, max_w: float = 260) -> QRectF:
    """A speech bubble whose tail points at `anchor` (from above)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(font(10))
    metrics = p.fontMetrics()
    rect = metrics.boundingRect(0, 0, int(max_w), 1000, Qt.TextWordWrap, text)
    w, h = rect.width() + 22, rect.height() + 16
    box = QRectF(anchor.x() - w / 2, anchor.y() - h - 12, w, h)
    path = QPainterPath()
    path.addRoundedRect(box, 10, 10)
    tail = QPainterPath()
    tail.moveTo(anchor.x() - 7, box.bottom() - 1)
    tail.lineTo(anchor.x(), anchor.y())
    tail.lineTo(anchor.x() + 7, box.bottom() - 1)
    tail.closeSubpath()
    p.setPen(pen(INK, 2))
    p.setBrush(PAPER)
    p.drawPath(path.united(tail))
    p.setPen(INK)
    p.drawText(box.adjusted(11, 8, -11, -8), Qt.TextWordWrap | Qt.AlignCenter, text)
    p.restore()
    return box


def draw_sign(p: QPainter, cx: float, top: float, text: str) -> None:
    """The little wooden sign the inventor raises ("Want me to do it?")."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(font(9.5, bold=True))
    w = p.fontMetrics().horizontalAdvance(text) + 20
    box = QRectF(cx - w / 2, top, w, 26)
    p.setPen(pen(INK, 2))
    p.setBrush(CREAM)
    p.drawRoundedRect(box, 6, 6)
    p.drawText(box, Qt.AlignCenter, text)
    p.restore()


# --- tangible objects ----------------------------------------------------------------------

def draw_card(p: QPainter, rect: QRectF, lines: list[str], tilt: float = 0.0, opacity: float = 1.0,
              accent: QColor = TEAL, image=None) -> None:
    """A data row as a small card: an accent strip, then its values (or its screen crop)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setOpacity(opacity)
    c = rect.center()
    p.translate(c)
    p.rotate(tilt)
    r = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
    p.setPen(Qt.NoPen)
    p.setBrush(SHADOW)
    p.drawRoundedRect(r.translated(2, 3), 7, 7)
    p.setPen(pen(INK, 1.6))
    p.setBrush(PAPER)
    p.drawRoundedRect(r, 7, 7)
    p.setPen(Qt.NoPen)
    p.setBrush(accent)
    p.drawRoundedRect(QRectF(r.left(), r.top(), 7, r.height()), 3, 3)
    inner = r.adjusted(12, 5, -6, -5)
    if image is not None and not image.isNull():
        p.drawImage(inner, image)
    else:
        p.setPen(INK)
        p.setFont(font(7.5, bold=True))
        y = inner.top()
        for i, line in enumerate(lines[:3]):
            if i == 1:
                p.setFont(font(7))
            p.drawText(QRectF(inner.left(), y, inner.width(), 12), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(line, Qt.ElideRight, int(inner.width())))
            y += 12
    p.restore()


BLOCK_COLORS = {"read_row": TEAL, "fill_field": MUSTARD, "click": CORAL, "save": CORAL,
                "wait": TEAL_LIGHT, "verify": TEAL}


def draw_block(p: QPainter, rect: QRectF, block_type: str, title: str, state: str = "idle", t: float = 0.0) -> None:
    """One block of a machine. state: idle | active | done | error | building."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor(BLOCK_COLORS.get(block_type, MUSTARD))
    if state == "building":
        p.setOpacity(0.35 + 0.65 * min(1.0, (t % 1.0) * 2))
    if state == "active":
        color = color.lighter(118)
    p.setPen(pen(INK, 2))
    p.setBrush(color)
    p.drawRoundedRect(rect, 8, 8)
    # bolts in the corners
    p.setPen(Qt.NoPen)
    p.setBrush(INK)
    for dx, dy in ((5, 5), (rect.width() - 5, 5), (5, rect.height() - 5), (rect.width() - 5, rect.height() - 5)):
        p.drawEllipse(QPointF(rect.left() + dx, rect.top() + dy), 1.6, 1.6)
    p.setPen(INK)
    p.setFont(font(7.5, bold=True))
    p.drawText(rect.adjusted(6, 4, -6, -4), Qt.AlignCenter | Qt.TextWordWrap, title)
    if state == "done":
        p.setPen(pen(TEAL, 2.5))
        cx, cy = rect.right() - 9, rect.top() + 9
        p.drawLine(QPointF(cx - 4, cy), QPointF(cx - 1, cy + 3))
        p.drawLine(QPointF(cx - 1, cy + 3), QPointF(cx + 5, cy - 4))
    p.restore()
    if state == "error":
        draw_sparks(p, QPointF(rect.right() - 4, rect.top() + 4), t, 1.1)


def draw_stamp(p: QPainter, center: QPointF, radius: float, marks: list[bool], label: str,
               offset: float = 0.0, t: float = 0.0) -> None:
    """Two overlapping round stamps (the user's demo and the machine's run); every field that
    matches lights up around the rim."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    for i, color in enumerate((TEAL, MUSTARD)):
        c = QPointF(center.x() + (i - 0.5) * offset, center.y())
        p.setPen(pen(color.darker(120), 3))
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 70))
        p.drawEllipse(c, radius, radius)
    n = max(1, len(marks))
    for i, ok in enumerate(marks):
        a = -math.pi / 2 + i * 2 * math.pi / n
        dot = QPointF(center.x() + math.cos(a) * radius * 0.78, center.y() + math.sin(a) * radius * 0.78)
        p.setPen(Qt.NoPen)
        lit = ok and (t * 6 > i)
        p.setBrush(TEAL if lit else (CORAL if not ok else QColor(200, 200, 200)))
        p.drawEllipse(dot, 4, 4)
    p.setPen(INK)
    p.setFont(font(8, bold=True))
    p.drawText(QRectF(center.x() - radius, center.y() - 10, radius * 2, 20), Qt.AlignCenter, label)
    p.restore()


def draw_engine(p: QPainter, rect: QRectF, level: str, t: float) -> None:
    """The machine's engine: a gear whose speed shows the compile level (page < network)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    speed = {"screen": 0.6, "page": 1.6, "network": 4.0}.get(level, 1.0)
    c = rect.center()
    r = min(rect.width(), rect.height()) / 2 - 2
    p.translate(c)
    p.rotate((t * speed * 90) % 360)
    p.setPen(pen(INK, 2))
    p.setBrush(TEAL if level == "network" else MUSTARD_DARK)
    path = QPainterPath()
    for i in range(16):
        a = i * math.pi / 8
        rr = r if i % 2 == 0 else r * 0.78
        pt = QPointF(math.cos(a) * rr, math.sin(a) * rr)
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(CREAM)
    p.drawEllipse(QPointF(0, 0), r * 0.35, r * 0.35)
    p.restore()
    p.save()
    p.setPen(INK)
    p.setFont(font(7, bold=True))
    p.drawText(QRectF(rect.left() - 20, rect.bottom() + 1, rect.width() + 40, 12), Qt.AlignCenter, level)
    p.restore()
