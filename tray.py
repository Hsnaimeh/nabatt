#!/usr/bin/env python3
"""Nabatt -- system-tray applet.

Sits in the notification area whenever you are logged in. The icon colour
follows the current draw, the tooltip carries the exact numbers, and the menu
opens the dashboard as a desktop window or restarts the two loggers.

Depends on pystray + pillow (pip install pystray pillow).
"""
import json, os, subprocess, sys, threading, time, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw            # noqa: E402
import pystray                              # noqa: E402
from pystray import MenuItem as Item, Menu  # noqa: E402

CREATE_NO_WINDOW = 0x08000000


from paths import cfg, LOGS                      # noqa: E402

CFG = cfg()
PORT = int(CFG.get("port", 8099))
CUR = CFG.get("currency", "JOD")
URL = "http://127.0.0.1:%d" % PORT   # explicit IPv4: never waits on a ::1 probe

# colour stops: watts -> icon colour, so a glance at the tray tells you the load
STOPS = [(0, (61, 220, 151)), (250, (124, 200, 120)), (400, (255, 176, 46)),
         (600, (255, 120, 71)), (800, (255, 95, 86))]


def load_colour(w):
    if w is None:
        return (110, 125, 145)
    for i in range(len(STOPS) - 1):
        (w0, c0), (w1, c1) = STOPS[i], STOPS[i + 1]
        if w <= w1:
            t = 0 if w1 == w0 else max(0.0, (w - w0) / (w1 - w0))
            return tuple(int(c0[j] + (c1[j] - c0[j]) * t) for j in range(3))
    return STOPS[-1][1]


BOLT = [(0.58, 0.04), (0.20, 0.55), (0.44, 0.55), (0.38, 0.96),
        (0.80, 0.42), (0.54, 0.42), (0.62, 0.04)]


def make_icon(colour, size=64):
    ss = 4
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = n * 0.05
    inner = n - pad * 2
    d.polygon([(pad + x * inner, pad + y * inner) for x, y in BOLT],
              fill=colour + (255,))
    return img.resize((size, size), Image.LANCZOS)


def fetch():
    try:
        with urllib.request.urlopen(URL + "/api/data", timeout=6) as r:
            return json.load(r)
    except Exception:
        return None


def ps(script, wait=False):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", os.path.join(ROOT, script)]
    p = subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)
    if wait:
        p.wait()


def open_app_window(_=None):
    ps("open-app.ps1")


def open_in_browser(_=None):
    os.startfile(URL)                                   # noqa: S606


def open_logs(_=None):
    os.startfile(LOGS)                                  # noqa: S606


def restart_tracking(_=None):
    def run():
        ps("stop-power-tracking.ps1", wait=True)
        time.sleep(1.5)
        ps("start-power-tracking.ps1")
    threading.Thread(target=run, daemon=True).start()


state = {"d": None}


def line(fmt, key, d=None, default="—"):
    d = d or state["d"]
    if not d or d.get(key) is None:
        return default
    return fmt.format(d[key])


def menu_status(_):
    d = state["d"]
    if not d:
        return "Dashboard not responding"
    live = "live" if d.get("live") else "stale"
    return "%s W  ·  %s" % (round(d.get("watts", 0)), live)


def menu_today(_):
    d = state["d"]
    if not d:
        return "—"
    return "Today   %.3f kWh   %.3f %s" % (d.get("today_kwh", 0), d.get("today_cost", 0), CUR)


def menu_month(_):
    d = state["d"]
    if not d:
        return "—"
    return "Month   %.2f kWh   %.2f %s" % (d.get("total_kwh", 0), d.get("total_cost", 0), CUR)


def menu_top(_):
    d = state["d"]
    apps = [a for a in (d or {}).get("live_apps", []) if not a.get("baseline")]
    if not apps:
        return "Top app   —"
    a = apps[0]
    return "Top app   %s  (%.0f W)" % (a["app"], a["w"])


icon = pystray.Icon(
    "Nabatt",
    make_icon(load_colour(None)),
    "Nabatt",
    Menu(
        Item(menu_status, open_app_window, default=True, enabled=True),
        Item(menu_today, None, enabled=False),
        Item(menu_month, None, enabled=False),
        Item(menu_top, None, enabled=False),
        Menu.SEPARATOR,
        Item("Open dashboard window", open_app_window),
        Item("Open in browser", open_in_browser),
        Item("Open log folder", open_logs),
        Menu.SEPARATOR,
        Item("Restart logging", restart_tracking),
        Item("Quit tray (logging keeps running)", lambda: icon.stop()),
    ),
)


def watchdog(d):
    """Bring the loggers back if they stop writing.

    Only acts after several consecutive stale reads, and at most once every few
    minutes, so a slow moment never triggers a restart storm. Stopping tracking
    properly (stop-power-tracking.ps1) also kills this tray, so a deliberate
    stop is never undone.
    """
    stale = d is None or not d.get("live")
    if not stale:
        state["stale_for"] = 0
        return
    state["stale_for"] = state.get("stale_for", 0) + 1
    if state["stale_for"] < STALE_LIMIT:
        return
    if time.monotonic() - state.get("last_revive", -1e9) < REVIVE_COOLDOWN:
        return
    state["last_revive"] = time.monotonic()
    state["stale_for"] = 0
    ps("start-power-tracking.ps1")          # idempotent: starts only what is down


POLL_SECONDS = 5
STALE_LIMIT = 12                            # ~1 minute of no fresh samples
REVIVE_COOLDOWN = 300                       # never more than once every 5 min


def poll():
    while True:
        d = fetch()
        state["d"] = d
        watchdog(d)
        if d is None:
            icon.icon = make_icon(load_colour(None))
            icon.title = "Nabatt — dashboard not responding"
        else:
            w = d.get("watts") or 0
            icon.icon = make_icon(load_colour(w if d.get("live") else None))
            top = [a for a in d.get("live_apps", []) if not a.get("baseline")]
            tip = "Nabatt %.0f W  ·  today %.3f kWh / %.3f %s\nmonth %.2f %s" % (
                w, d.get("today_kwh", 0), d.get("today_cost", 0), CUR,
                d.get("total_cost", 0), CUR)
            if top:
                tip += "\ntop: %s (%.0f W)" % (top[0]["app"], top[0]["w"])
            if not d.get("live"):
                tip = "STALE — logger may be stopped\n" + tip
            icon.title = tip[:127]          # Windows tooltip limit
        try:
            icon.update_menu()
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    icon.run()
