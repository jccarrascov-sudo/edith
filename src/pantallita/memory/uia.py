"""Windows UI Automation: compact, text-only view of what is on screen.

Only visible, named, interactive elements (plus short labels) are kept, one line each:
    [id] type "name" = "value" @x,y,w,h      (coordinates relative to the window)
Password fields (IsPassword) never have their value read. The keyboard is never touched.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import uiautomation as auto

INTERACTIVE = {
    "ButtonControl": "button", "EditControl": "edit", "ComboBoxControl": "combo",
    "CheckBoxControl": "check", "RadioButtonControl": "radio", "HyperlinkControl": "link",
    "MenuItemControl": "menuitem", "TabItemControl": "tab", "ListItemControl": "item",
    "SliderControl": "slider", "SpinnerControl": "spinner", "TreeItemControl": "treeitem",
    "SplitButtonControl": "button", "DataItemControl": "cell",
}
LABELS = {"TextControl": "text", "HeaderControl": "header", "HeaderItemControl": "header"}


@dataclass
class Element:
    kind: str
    name: str
    value: str | None
    rect: tuple[int, int, int, int]  # left, top, right, bottom in screen pixels
    is_password: bool

    def line(self, idx: int, origin: tuple[int, int]) -> str:
        x, y = self.rect[0] - origin[0], self.rect[1] - origin[1]
        w, h = self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]
        value = ' = "***"' if self.is_password else (f' = "{self.value}"' if self.value else "")
        return f'[{idx}] {self.kind} "{self.name}"{value} @{x},{y},{w},{h}'


def _value(control: auto.Control) -> str | None:
    try:
        if control.GetPropertyValue(auto.PropertyId.IsPasswordProperty):
            return None
        pattern = control.GetPattern(auto.PatternId.ValuePattern)
        return (pattern.Value or "")[:120] if pattern else None
    except Exception:
        return None


def _rect(control: auto.Control) -> tuple[int, int, int, int]:
    r = control.BoundingRectangle
    return r.left, r.top, r.right, r.bottom


def window_elements(hwnd: int, max_elements: int = 400, max_depth: int = 40,
                    budget_s: float = 3.0, include_labels: bool = True) -> list[Element]:
    """Visible elements of a top-level window, in tree order."""
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return []
    win = _rect(root)
    deadline = time.monotonic() + budget_s
    out: list[Element] = []
    for control, _depth in auto.WalkControl(root, includeTop=False, maxDepth=max_depth):
        if len(out) >= max_elements or time.monotonic() > deadline:
            break
        try:
            type_name = control.ControlTypeName
            kind = INTERACTIVE.get(type_name) or (LABELS.get(type_name) if include_labels else None)
            if not kind or control.IsOffscreen:
                continue
            rect = _rect(control)
            if rect[2] <= rect[0] or rect[3] <= rect[1] or rect[2] < win[0] or rect[0] > win[2] \
                    or rect[3] < win[1] or rect[1] > win[3]:
                continue
            name = (control.Name or "").strip().replace("\n", " ")[:80]
            is_password = bool(control.GetPropertyValue(auto.PropertyId.IsPasswordProperty))
            value = None if is_password or kind not in ("edit", "combo", "slider", "spinner") else _value(control)
            if not name and not value:
                continue
            out.append(Element(kind, name, value, rect, is_password))
        except Exception:
            continue
    return out


def compressed_tree(hwnd: int, **kwargs) -> str:
    """Compact text observation of a window (see module docstring); duplicates removed."""
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return ""
    win = _rect(root)
    origin = win[:2]
    seen: set[tuple] = set()
    lines = [f'window "{(root.Name or "").strip()[:100]}" size {win[2] - win[0]}x{win[3] - win[1]}']
    for el in window_elements(hwnd, **kwargs):
        key = (el.kind, el.name, el.value, el.rect)
        if key in seen:
            continue
        seen.add(key)
        lines.append(el.line(len(lines), origin))
    return "\n".join(lines)


def raw_tree(hwnd: int, max_elements: int = 2000, max_depth: int = 40, budget_s: float = 6.0) -> str:
    """Naive linearized tree (every element, every basic property) — the baseline we compress."""
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return ""
    deadline = time.monotonic() + budget_s
    lines = []
    for control, depth in auto.WalkControl(root, includeTop=True, maxDepth=max_depth):
        if len(lines) >= max_elements or time.monotonic() > deadline:
            break
        try:
            r = control.BoundingRectangle
            lines.append(f'{"  " * depth}{control.ControlTypeName} Name="{control.Name}" '
                         f'AutomationId="{control.AutomationId}" ClassName="{control.ClassName}" '
                         f'Rect=({r.left},{r.top},{r.right},{r.bottom}) Offscreen={control.IsOffscreen} '
                         f'Enabled={control.IsEnabled}')
        except Exception:
            continue
    return "\n".join(lines)


def element_at(x: int, y: int) -> Element | None:
    """The UIA element under a screen point (used for clicks)."""
    try:
        control = auto.ControlFromPoint(x, y)
    except Exception:
        return None
    if control is None:
        return None
    is_password = bool(control.GetPropertyValue(auto.PropertyId.IsPasswordProperty))
    kind = INTERACTIVE.get(control.ControlTypeName) or LABELS.get(control.ControlTypeName) \
        or control.ControlTypeName.removesuffix("Control").lower()
    return Element(kind, (control.Name or "").strip()[:80], None if is_password else _value(control),
                   _rect(control), is_password)


BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
_URLISH = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?(?:localhost|[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})"
                     r"(?::\d+)?(?:[/?#]\S*)?$", re.I)


def normalize_url(text: str) -> str:
    """Browsers hide the scheme in the address bar; local addresses are http, the rest https."""
    text = text.strip()
    if "://" in text:
        return text
    host = text.split("/")[0].split(":")[0]
    local = host in ("localhost", "127.0.0.1") or host.startswith(("192.168.", "10."))
    return ("http://" if local else "https://") + text


def address_bar_url(hwnd: int, budget_s: float = 0.6) -> str | None:
    """URL in a browser window's address bar. Web page content (Document) is never entered."""
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return None
    deadline = time.monotonic() + budget_s
    stack = [(root, 0)]
    while stack and time.monotonic() < deadline:
        control, depth = stack.pop()
        try:
            type_name = control.ControlTypeName
            if type_name == "DocumentControl":
                continue
            if type_name == "EditControl" and not control.GetPropertyValue(auto.PropertyId.IsPasswordProperty):
                value = (_value(control) or "").strip()
                if _URLISH.match(value):
                    return normalize_url(value)
            if depth < 14:
                children = control.GetChildren()
                stack.extend((c, depth + 1) for c in reversed(children))
        except Exception:
            continue
    return None
