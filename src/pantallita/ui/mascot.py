"""The companion: a small always-on-top window with the inventor, walking on the taskbar.

Cheap by design: the window is only as big as the inventor plus his speech bubble, it is
moved (not repainted) to walk, and it animates at 30 fps only while something moves; when
he just stands there it repaints ~8 times a second (eyes and blinking). Fully transparent
pixels let clicks through, so only the inventor himself can be clicked.
"""

from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from . import art, winenv

W, H = 340, 230          # window size (logical px); the inventor stands at the bottom center
SCALE = 1.0
FEET = H - 6
WALK_SPEED = 70.0        # px / s
ACTIVE_MS, IDLE_MS = 33, 125
EDGE = 135               # keep this far from the screen edges so his speech bubble fits


class Mascot(QWidget):
    clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(W, H)
        self.t0 = time.monotonic()
        self.state = "idle"
        self.mood: str | None = None      # set by the app: catch, build, verify, confused, sign...
        self.mood_until = 0.0
        self.bubble: str | None = None
        self.bubble_until = 0.0
        self.sign: str | None = None
        self.x = 0.0                       # inventor's x (screen, logical px)
        self.target_x = 0.0
        self.ground_y = 0.0                # feet y (screen, logical px)
        self.ledge: tuple | None = None    # (hwnd, y, x_left, x_right) in logical px
        self.jump: tuple | None = None     # (start time, from (x, y), to (x, y))
        self.facing = 1
        self.next_wander = time.monotonic() + 4
        self.next_ledge = time.monotonic() + random.uniform(30, 60)
        self.ledge_until = 0.0
        self.blink_until = 0.0
        self.next_blink = time.monotonic() + 3
        self.last_cursor = QCursor.pos()
        self.cursor_moved_at = 0.0
        self.hidden_for_fullscreen = False
        self.in_workshop = False
        self.workshop_x: float | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(IDLE_MS)
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.check_environment)
        self.check_timer.start(1000)
        self.place_on_taskbar(initial=True)

    # --- geometry -------------------------------------------------------------------------
    def screen_geo(self):
        screen = QGuiApplication.primaryScreen()
        return screen.geometry(), screen.availableGeometry(), screen.devicePixelRatio()

    def taskbar_y(self) -> float:
        _full, avail, _dpr = self.screen_geo()
        return avail.bottom() + 1

    def place_on_taskbar(self, initial: bool = False) -> None:
        full, avail, _ = self.screen_geo()
        self.ground_y = self.taskbar_y()
        if initial:
            self.x = self.target_x = avail.right() - 300
        self.move_window()

    def move_window(self) -> None:
        self.move(int(self.x - W / 2), int(self.ground_y - FEET))

    def inventor_rect(self) -> QRectF:
        return QRectF(W / 2 - 26, FEET - 72, 52, 74)

    # --- public API used by the app ---------------------------------------------------------
    def set_mood(self, mood: str | None, seconds: float = 3.0) -> None:
        self.mood = mood
        self.mood_until = time.monotonic() + seconds if mood else 0
        self.update()

    def say(self, text: str, seconds: float = 4.0) -> None:
        self.bubble = text
        self.bubble_until = time.monotonic() + seconds
        self.update()

    def show_sign(self, text: str | None) -> None:
        self.sign = text
        self.update()

    def walk_to(self, x: float) -> None:
        _full, avail, _ = self.screen_geo()
        self.target_x = max(avail.left() + EDGE, min(avail.right() - EDGE, x))

    def go_to_workshop(self, shelf_x: float) -> None:
        self.in_workshop = True
        self.workshop_x = shelf_x
        self.leave_ledge()
        self.walk_to(shelf_x)

    def leave_workshop(self) -> None:
        self.in_workshop = False
        _full, avail, _ = self.screen_geo()
        self.walk_to(avail.right() - 300)

    # --- behaviour ------------------------------------------------------------------------
    def leave_ledge(self) -> None:
        if self.ledge:
            self.start_jump(self.x, self.taskbar_y())
            self.ledge = None

    def start_jump(self, to_x: float, to_y: float) -> None:
        self.jump = (time.monotonic(), (self.x, self.ground_y), (to_x, to_y))

    def check_environment(self) -> None:
        full, _avail, dpr = self.screen_geo()
        phys = (int(full.left() * dpr), int(full.top() * dpr), int(full.right() * dpr), int(full.bottom() * dpr))
        hide = winenv.should_hide(phys)
        if hide and not self.hidden_for_fullscreen:
            self.hidden_for_fullscreen = True
            self.hide()
        elif not hide and self.hidden_for_fullscreen:
            self.hidden_for_fullscreen = False
            self.show()
        if self.ledge and not self.jump:  # the window under his feet moved or closed?
            ledges = {l[0]: l for l in self.find_ledges()}
            current = ledges.get(self.ledge[0])
            if not current or abs(current[1] - self.ledge[1]) > 2:
                self.ledge = None
                self.start_jump(self.x, self.taskbar_y())

    def find_ledges(self) -> list[tuple]:
        full, _avail, dpr = self.screen_geo()
        phys = (int(full.left() * dpr), int(full.top() * dpr), int(full.right() * dpr), int(full.bottom() * dpr))
        own = {int(self.winId())}
        return [(h, y / dpr, l / dpr, r / dpr) for h, y, l, r in winenv.window_ledges(phys, own)]

    def tick(self) -> None:
        now = time.monotonic()
        t = now - self.t0
        if self.mood and now > self.mood_until:
            self.mood = None
        if self.bubble and now > self.bubble_until:
            self.bubble = None
        cursor = QCursor.pos()
        if cursor != self.last_cursor:
            self.cursor_moved_at = now
            self.last_cursor = cursor
        if now > self.next_blink:
            self.blink_until = now + 0.14
            self.next_blink = now + random.uniform(2.5, 6)

        moving = False
        if self.jump:
            start, (fx, fy), (tx, ty) = self.jump
            k = min(1.0, (now - start) / 0.6)
            self.x = fx + (tx - fx) * k
            self.ground_y = fy + (ty - fy) * k - math.sin(k * math.pi) * 60
            if k >= 1:
                self.jump = None
                self.ground_y = ty
            moving = True
        else:
            if not self.mood and not self.in_workshop:
                self.wander(now, cursor)
            dx = self.target_x - self.x
            if abs(dx) > 1.5:
                step = math.copysign(min(abs(dx), WALK_SPEED * (ACTIVE_MS / 1000)), dx)
                self.x += step
                self.facing = 1 if dx > 0 else -1
                moving = True
                if self.ledge and not (self.ledge[2] <= self.x <= self.ledge[3]):
                    self.leave_ledge()
        self.state = "walk" if moving and not self.jump else ("idle" if not self.jump else "catch")
        if moving:
            self.move_window()
        animating = moving or bool(self.mood) or bool(self.bubble)
        self.timer.setInterval(ACTIVE_MS if animating else IDLE_MS)
        self.update()

    def wander(self, now: float, cursor: QPoint) -> None:
        _full, avail, _ = self.screen_geo()
        near = abs(cursor.x() - self.x) < 150 and abs(cursor.y() - self.ground_y) < 160
        if near and now - self.cursor_moved_at < 1.0:
            # the user is working right here: step aside
            away = self.x + (220 if cursor.x() < self.x else -220)
            if self.ledge:
                away = max(self.ledge[2], min(self.ledge[3], away))
            self.walk_to(away)
            self.next_wander = now + random.uniform(6, 12)
            return
        if self.ledge and now > self.ledge_until:
            self.leave_ledge()
            return
        if not self.ledge and now > self.next_ledge:
            self.next_ledge = now + random.uniform(45, 120)
            ledges = self.find_ledges()
            if ledges:
                hwnd, y, l, r = random.choice(ledges[:4])
                _full, avail, _ = self.screen_geo()
                l, r = max(l, avail.left() + EDGE), min(r, avail.right() - EDGE)
                if r - l < 60:
                    return
                self.ledge = (hwnd, y, l, r)
                self.ledge_until = now + random.uniform(10, 25)
                self.start_jump(max(l, min(r, self.x)), y)
                return
        if now > self.next_wander:
            self.next_wander = now + random.uniform(8, 20)
            if self.ledge:
                self.walk_to(random.uniform(self.ledge[2], self.ledge[3]))
            elif random.random() < 0.6:
                self.walk_to(self.x + random.uniform(-260, 260))

    # --- drawing and input ----------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        t = time.monotonic() - self.t0
        state = self.mood or self.state
        cursor = QCursor.pos() - self.pos()
        dx, dy = cursor.x() - W / 2, cursor.y() - (FEET - 40)
        dist = max(1.0, math.hypot(dx, dy))
        look = (dx / dist, dy / dist) if state in ("idle", "walk", "sign") else (0.0, 0.2)
        art.draw_mascot(p, W / 2, FEET, SCALE, state, t, look, self.facing,
                        blink=time.monotonic() < self.blink_until)
        if state == "rewind":
            art.draw_film(p, W / 2 + 22 * self.facing if self.facing > 0 else W / 2 - 130, FEET - 50, t)
        if self.sign:
            art.draw_sign(p, W / 2, FEET - 112, self.sign)
        if self.bubble:
            art.draw_bubble(p, QPointF(W / 2, FEET - (118 if self.sign else 80)), self.bubble, max_w=240)
        p.end()

    def mousePressEvent(self, event) -> None:
        if self.inventor_rect().contains(event.position()):
            self.clicked.emit()
