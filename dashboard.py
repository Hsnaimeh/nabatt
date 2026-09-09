#!/usr/bin/env python3
"""Live power / cost / per-app dashboard.

Stdlib only. Reads the CSVs written by power-logger.ps1 (whole-machine energy,
one file per month) and app-logger.ps1 (per-application attribution, one file
per day).

Endpoints
    /api/data              live snapshot + today + month + projections
    /api/history           every day on record, with coverage and gaps
    /api/day?d=YYYY-MM-DD  one day in full: 24 hourly buckets, apps, gaps
    /api/apps?range=...    app breakdown over today | 7d | 30d | all
"""
import csv, json, os, sys, calendar, socket, threading, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from paths import APP as ROOT, LOGS, WEB, cfg   # noqa: E402

# --demo swaps the log folder and the config for a generated fortnight, so the
# dashboard can be explored on any machine. Everything downstream of this is
# the ordinary code path reading ordinary CSVs -- nothing is stubbed.
DEMO = "--demo" in sys.argv
if DEMO:
    import demo_data                            # noqa: E402
    LOGS, _DEMO_CFG, _n_power, _n_apps = demo_data.setup()

    def cfg():                                  # noqa: F811
        return dict(_DEMO_CFG)


def parse_dt(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError("bad datetime: %r" % s)


# --------------------------------------------------------------------------
# CSV loading, cached on (mtime, size) so repeated polls stay cheap
# --------------------------------------------------------------------------
_cache = {}
_lock = threading.Lock()


def _cached(path, loader):
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (st.st_mtime, st.st_size)
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
    rows = loader(path)
    with _lock:
        _cache[path] = (key, rows)
    return rows


def _load_power(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "ts": parse_dt(r["ts"]),
                    "gpu": float(r["gpu_w"]),
                    "gu": float(r["gpu_util"]),
                    "cu": float(r["cpu_util"]),
                    "cpu": float(r["cpu_w"]),
                    "wall": float(r["wall_w"]),
                    "wh": float(r["wh"]),
                })
            except (KeyError, ValueError, TypeError):
                continue
    rows.sort(key=lambda r: r["ts"])
    return rows


def _load_apps(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "ts": parse_dt(r["ts"]),
                    "app": r["app"],
                    "pids": int(float(r.get("pids") or 0)),
                    "w": float(r["watts"]),
                    "wh": float(r["wh"]),
                    "gw": float(r.get("gpu_w") or 0),
                    "cw": float(r.get("cpu_w") or 0),
                })
            except (KeyError, ValueError, TypeError):
                continue
    rows.sort(key=lambda r: r["ts"])
    return rows


def month_keys():
    if not os.path.isdir(LOGS):
        return []
    return sorted(n[6:-4] for n in os.listdir(LOGS)
                  if n.startswith("power-") and n.endswith(".csv"))


def app_day_keys():
    if not os.path.isdir(LOGS):
        return []
    return sorted(n[5:-4] for n in os.listdir(LOGS)
                  if n.startswith("apps-") and n.endswith(".csv"))


def read_month(key):
    return _cached(os.path.join(LOGS, "power-%s.csv" % key), _load_power)


def read_app_day(day):
    return _cached(os.path.join(LOGS, "apps-%s.csv" % day), _load_apps)


def all_power_rows():
    out = []
    for k in month_keys():
        out.extend(read_month(k))
    out.sort(key=lambda r: r["ts"])
    return out


# --------------------------------------------------------------------------
# friendly application names
# --------------------------------------------------------------------------
FRIENDLY = {
    "__baseline__": "Idle baseline (PSU, board, fans, RAM)",
    "chrome": "Google Chrome", "msedge": "Microsoft Edge",
    "msedgewebview2": "Edge WebView runtime", "firefox": "Firefox",
    "dwm": "Windows desktop compositor", "explorer": "Windows Explorer",
    "System": "Windows kernel", "MsMpEng": "Microsoft Defender",
    "WmiPrvSE": "Windows WMI service", "svchost": "Windows services",
    "SearchHost": "Windows Search", "StartMenuExperienceHost": "Start Menu",
    "TextInputHost": "Windows text input", "ctfmon": "Windows text input",
    "WindowsTerminal": "Windows Terminal", "powershell": "PowerShell",
    "pwsh": "PowerShell 7", "cmd": "Command Prompt", "conhost": "Console host",
    "python": "Python", "pythonw": "Python (windowless)",
    "node": "Node.js", "claude": "Claude Code",
    "Code": "VS Code", "devenv": "Visual Studio",
    "llama-server": "llama.cpp server (local LLM)", "llama-cli": "llama.cpp",
    "ollama": "Ollama", "ollama app": "Ollama",
    "koboldcpp": "KoboldCpp",
    "ffmpeg": "FFmpeg (video encode)", "ffprobe": "FFprobe",
    "whisper": "Whisper (transcription)", "whisper-cli": "Whisper.cpp",
    "MediaSDKApplication": "Insta360 Studio", "Insta360Studio": "Insta360 Studio",
    "Adobe Premiere Pro": "Premiere Pro", "AfterFX": "After Effects",
    "Photoshop": "Photoshop", "Resolve": "DaVinci Resolve",
    "blender": "Blender", "obs64": "OBS Studio",
    "steam": "Steam", "steamwebhelper": "Steam",
    "nvcontainer": "NVIDIA container", "NVIDIA Share": "NVIDIA overlay",
    "Copilot": "Windows Copilot", "M365Copilot": "Microsoft 365 Copilot",
    "Discord": "Discord", "Spotify": "Spotify", "Teams": "Microsoft Teams",
    "quick-look": "QuickLook", "CrossDeviceResume": "Windows Phone Link",
}

BASELINE = "__baseline__"


def friendly(name):
    return FRIENDLY.get(name, name)


# --------------------------------------------------------------------------
# per-day statistics
# --------------------------------------------------------------------------
def day_stats(rows, d, sample_s, rate):
    """The complete record for one calendar day; rows must be that day's rows."""
    now = datetime.now()
    gap_limit = sample_s * 3

    kwh = sum(r["wh"] for r in rows) / 1000.0
    covered_h = sum(r["wh"] / r["wall"] for r in rows if r["wall"] > 0)

    hourly = []
    for h in range(24):
        hourly.append({"h": h, "wh": 0.0, "sec": 0.0, "peak": 0.0})
    for r in rows:
        b = hourly[r["ts"].hour]
        b["wh"] += r["wh"]
        if r["wall"] > 0:
            b["sec"] += r["wh"] / r["wall"] * 3600.0
        if r["wall"] > b["peak"]:
            b["peak"] = r["wall"]
    for b in hourly:
        wh, sec = b.pop("wh"), b.pop("sec")
        b["kwh"] = round(wh / 1000.0, 4)
        b["cost"] = round(wh / 1000.0 * rate, 4)
        b["avg_w"] = round(wh / (sec / 3600.0), 1) if sec > 0 else 0.0
        b["coverage"] = round(min(1.0, sec / 3600.0), 3)
        b["peak"] = round(b["peak"], 1)

    # stretches with no samples: machine off, asleep, or logger stopped
    gaps = []
    day_start = datetime.combine(d, datetime.min.time())
    day_end = min(day_start + timedelta(days=1), now)
    cursor = day_start
    for r in rows:
        if (r["ts"] - cursor).total_seconds() > gap_limit:
            gaps.append({"from": cursor.strftime("%H:%M"),
                         "to": r["ts"].strftime("%H:%M"),
                         "hours": round((r["ts"] - cursor).total_seconds() / 3600.0, 2)})
        if r["ts"] > cursor:
            cursor = r["ts"]
    if (day_end - cursor).total_seconds() > gap_limit:
        gaps.append({"from": cursor.strftime("%H:%M"),
                     "to": day_end.strftime("%H:%M"),
                     "hours": round((day_end - cursor).total_seconds() / 3600.0, 2)})

    peak = max(rows, key=lambda r: r["wall"]) if rows else None
    elapsed_h = max(0.25, (day_end - day_start).total_seconds() / 3600.0)

    return {
        "date": d.isoformat(),
        "weekday": d.strftime("%a"),
        "kwh": round(kwh, 4),
        "cost": round(kwh * rate, 3),
        "samples": len(rows),
        "on_hours": round(covered_h, 2),
        "coverage": round(min(1.0, covered_h / elapsed_h), 3),
        "avg_w": round(kwh * 1000.0 / covered_h, 1) if covered_h > 0.02 else 0.0,
        "peak_w": round(peak["wall"], 1) if peak else 0.0,
        "peak_at": peak["ts"].strftime("%H:%M") if peak else None,
        "min_w": round(min(r["wall"] for r in rows), 1) if rows else 0.0,
        "first_seen": rows[0]["ts"].strftime("%H:%M") if rows else None,
        "last_seen": rows[-1]["ts"].strftime("%H:%M") if rows else None,
        "gaps": gaps,
        "off_hours": round(sum(g["hours"] for g in gaps), 2),
        "hourly": hourly,
        "partial": d == now.date(),
    }


def group_by_day(rows):
    out = {}
    for r in rows:
        out.setdefault(r["ts"].date(), []).append(r)
    return out


def app_breakdown(days, rate, hour=None):
    """Aggregate per-app energy across a list of date objects.

    `hour` narrows it to a single hour of the clock (0-23), which is what the
    hour drill-down and the time-of-day filter are built on.
    """
    agg = {}
    for d in days:
        for r in read_app_day(d.isoformat()):
            if hour is not None and r["ts"].hour != hour:
                continue
            a = agg.setdefault(r["app"], {"wh": 0.0, "gwh": 0.0, "peak_w": 0.0,
                                          "sec": 0.0, "pids": 0})
            a["wh"] += r["wh"]
            if r["w"] > a["peak_w"]:
                a["peak_w"] = r["w"]
            if r["pids"] > a["pids"]:
                a["pids"] = r["pids"]
            if r["w"] > 0:
                a["sec"] += r["wh"] / r["w"] * 3600.0
                a["gwh"] += r["gw"] / r["w"] * r["wh"]
    total = sum(a["wh"] for a in agg.values()) or 1.0
    out = []
    for name, a in agg.items():
        out.append({
            "app": friendly(name), "raw": name, "baseline": name == BASELINE,
            "kwh": round(a["wh"] / 1000.0, 4),
            "cost": round(a["wh"] / 1000.0 * rate, 3),
            "share": round(a["wh"] / total, 4),
            "avg_w": round(a["wh"] / (a["sec"] / 3600.0), 1) if a["sec"] > 0 else 0.0,
            "peak_w": round(a["peak_w"], 1),
            "active_h": round(a["sec"] / 3600.0, 2),
            "gpu_share": round(a["gwh"] / a["wh"], 3) if a["wh"] > 0 else 0,
            "pids": a["pids"],
        })
    out.sort(key=lambda x: -x["kwh"])
    return out


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------
def api_data():
    c = cfg()
    rate = float(c["tariff_jd_per_kwh"])
    sample_s = float(c["sample_seconds"])
    now = datetime.now()
    rows = read_month(now.strftime("%Y-%m"))

    out = {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "rate": rate,
           "currency": c.get("currency", "JOD"), "samples": len(rows),
           "live": False, "demo": DEMO}

    days_in_month = calendar.monthrange(now.year, now.month)[1]
    month_end = datetime(now.year, now.month, days_in_month, 23, 59, 59)
    remaining_h = max(0.0, (month_end - now).total_seconds() / 3600.0)

    month_wh = sum(r["wh"] for r in rows)
    today = now.date()
    today_wh = sum(r["wh"] for r in rows if r["ts"].date() == today)
    logged_h = sum(r["wh"] / r["wall"] for r in rows if r["wall"] > 0)

    est_wh = 0.0
    if rows:
        try:
            start = parse_dt(str(c.get("billing_start", "")))
            gap_h = max(0.0, (rows[0]["ts"] - start).total_seconds() / 3600.0)
            est_wh = float(c.get("pre_log_estimate_w", 0)) * gap_h
        except ValueError:
            pass

    avg_w = (month_wh / logged_h) if logged_h > 0.05 else 0.0

    if rows:
        last = rows[-1]
        age = (now - last["ts"]).total_seconds()
        out["live"] = age < sample_s * 6
        out.update({"watts": round(last["wall"], 1), "gpu_w": round(last["gpu"], 1),
                    "cpu_w": round(last["cpu"], 1), "gpu_util": round(last["gu"]),
                    "cpu_util": round(last["cu"]),
                    "last_seen": last["ts"].strftime("%H:%M:%S"), "age_s": round(age)})
    else:
        out.update({"watts": 0, "gpu_w": 0, "cpu_w": 0, "gpu_util": 0,
                    "cpu_util": 0, "last_seen": "-", "age_s": None})

    def money(wh):
        return round(wh / 1000.0 * rate, 2)

    out["today_kwh"] = round(today_wh / 1000.0, 3)
    out["today_cost"] = money(today_wh)
    out["month_kwh"] = round(month_wh / 1000.0, 3)
    out["month_cost"] = money(month_wh)
    out["est_kwh"] = round(est_wh / 1000.0, 3)
    out["est_cost"] = money(est_wh)
    out["total_kwh"] = round((month_wh + est_wh) / 1000.0, 3)
    out["total_cost"] = money(month_wh + est_wh)
    out["avg_w"] = round(avg_w, 1)
    out["logged_h"] = round(logged_h, 2)
    out["remaining_h"] = round(remaining_h, 1)
    out["days_in_month"] = days_in_month

    def project(w):
        wh = month_wh + est_wh + w * remaining_h
        return {"w": round(w), "kwh": round(wh / 1000.0, 1), "cost": money(wh)}

    full_w = ((390 + float(c["cpu_max_w"]) + float(c["rest_of_system_w"])
               + float(c["monitors_w"])) / float(c["psu_efficiency"]))
    out["projection"] = {"at_avg": project(avg_w) if avg_w > 0 else None,
                         "at_current": project(out["watts"]) if out["watts"] else None,
                         "at_full": project(full_w)}

    cutoff = now - timedelta(hours=3)
    recent = [r for r in rows if r["ts"] >= cutoff] or rows[-200:]
    step = max(1, len(recent) // 200)
    out["series"] = [{"t": r["ts"].strftime("%H:%M"), "w": round(r["wall"], 1),
                      "g": round(r["gpu"], 1), "c": round(r["cpu"], 1)}
                     for r in recent[::step]]

    # what is drawing power right now, from the newest per-app sample
    apps_today = read_app_day(today.isoformat())
    live_apps = []
    if apps_today:
        newest = apps_today[-1]["ts"]
        for r in reversed(apps_today):
            if r["ts"] != newest:
                break
            live_apps.append({"app": friendly(r["app"]), "raw": r["app"],
                              "baseline": r["app"] == BASELINE,
                              "w": round(r["w"], 1), "pids": r["pids"],
                              "gpu_w": round(r["gw"], 1), "cpu_w": round(r["cw"], 1)})
        live_apps.sort(key=lambda x: -x["w"])
        out["apps_at"] = newest.strftime("%H:%M:%S")
        out["apps_age_s"] = round((now - newest).total_seconds())
    out["live_apps"] = live_apps
    out["today_apps"] = app_breakdown([today], rate)[:14]
    return out


def api_history():
    c = cfg()
    rate = float(c["tariff_jd_per_kwh"])
    sample_s = float(c["sample_seconds"])
    by_day = group_by_day(all_power_rows())
    base = {"rate": rate, "currency": c.get("currency", "JOD"),
            "app_days": app_day_keys()}
    if not by_day:
        base.update({"days": [], "first_day": None, "totals": {}})
        return base

    # every calendar day from the first record to today, so nothing is missing
    first = min(by_day)
    last = datetime.now().date()
    days = []
    d = first
    while d <= last:
        days.append(day_stats(by_day.get(d, []), d, sample_s, rate))
        d += timedelta(days=1)

    tot_kwh = sum(x["kwh"] for x in days)
    complete = [x for x in days if not x["partial"] and x["samples"]]
    base.update({
        "days": days,
        "first_day": first.isoformat(),
        "totals": {
            "kwh": round(tot_kwh, 3), "cost": round(tot_kwh * rate, 2),
            "days": len(days),
            "logged_days": len([x for x in days if x["samples"]]),
            "avg_day_kwh": round(sum(x["kwh"] for x in complete) / len(complete), 3) if complete else 0,
            "avg_day_cost": round(sum(x["cost"] for x in complete) / len(complete), 3) if complete else 0,
            "peak_w": round(max((x["peak_w"] for x in days), default=0), 1),
            "busiest": max(complete, key=lambda x: x["kwh"])["date"] if complete else None,
            "quietest": min(complete, key=lambda x: x["kwh"])["date"] if complete else None,
        },
    })
    return base


def api_day(dstr):
    c = cfg()
    rate = float(c["tariff_jd_per_kwh"])
    sample_s = float(c["sample_seconds"])
    d = datetime.strptime(dstr, "%Y-%m-%d").date()
    rows = [r for r in read_month(d.strftime("%Y-%m")) if r["ts"].date() == d]
    out = day_stats(rows, d, sample_s, rate)
    out["apps"] = app_breakdown([d], rate)
    out["currency"] = c.get("currency", "JOD")
    out["rate"] = rate
    step = max(1, len(rows) // 400)
    out["series"] = [{"t": r["ts"].strftime("%H:%M"), "w": round(r["wall"], 1),
                      "g": round(r["gpu"], 1), "c": round(r["cpu"], 1)}
                     for r in rows[::step]]
    return out


# --------------------------------------------------------------------------
# hour drill-down: what actually happened in one hour, and every hour on record
# --------------------------------------------------------------------------
def hour_app_index():
    """(date, hour) -> {app name: Wh}, built once per request from the cache."""
    idx = {}
    for key in app_day_keys():
        for r in read_app_day(key):
            k = (r["ts"].date(), r["ts"].hour)
            slot = idx.setdefault(k, {})
            slot[r["app"]] = slot.get(r["app"], 0.0) + r["wh"]
    return idx


def api_hour(dstr, h):
    """One hour, in full: minute-level curve plus the apps running in it."""
    c = cfg()
    rate = float(c["tariff_jd_per_kwh"])
    sample_s = float(c["sample_seconds"])
    d = datetime.strptime(dstr, "%Y-%m-%d").date()
    h = max(0, min(23, int(h)))

    rows = [r for r in read_month(d.strftime("%Y-%m"))
            if r["ts"].date() == d and r["ts"].hour == h]

    wh = sum(r["wh"] for r in rows)
    sec = sum(r["wh"] / r["wall"] * 3600.0 for r in rows if r["wall"] > 0)
    peak = max(rows, key=lambda r: r["wall"]) if rows else None
    low = min(rows, key=lambda r: r["wall"]) if rows else None

    # gaps inside the hour, so a partly-logged hour is never passed off as whole
    gaps = []
    start = datetime.combine(d, datetime.min.time()) + timedelta(hours=h)
    end = min(start + timedelta(hours=1), datetime.now())
    cursor = start
    for r in rows:
        if (r["ts"] - cursor).total_seconds() > sample_s * 3:
            gaps.append({"from": cursor.strftime("%H:%M:%S"),
                         "to": r["ts"].strftime("%H:%M:%S"),
                         "minutes": round((r["ts"] - cursor).total_seconds() / 60.0, 1)})
        if r["ts"] > cursor:
            cursor = r["ts"]
    if (end - cursor).total_seconds() > sample_s * 3:
        gaps.append({"from": cursor.strftime("%H:%M:%S"),
                     "to": end.strftime("%H:%M:%S"),
                     "minutes": round((end - cursor).total_seconds() / 60.0, 1)})

    step = max(1, len(rows) // 240)
    return {
        "date": d.isoformat(), "hour": h,
        "label": "%s %02d:00-%02d:59" % (d.isoformat(), h, h),
        "weekday": d.strftime("%a"),
        "currency": c.get("currency", "JOD"), "rate": rate,
        "kwh": round(wh / 1000.0, 5),
        "cost": round(wh / 1000.0 * rate, 5),
        "avg_w": round(wh / (sec / 3600.0), 1) if sec > 0 else 0.0,
        "peak_w": round(peak["wall"], 1) if peak else 0.0,
        "peak_at": peak["ts"].strftime("%H:%M:%S") if peak else None,
        "min_w": round(low["wall"], 1) if low else 0.0,
        "samples": len(rows),
        "coverage": round(min(1.0, sec / 3600.0), 3),
        "logged_min": round(sec / 60.0, 1),
        "gaps": gaps,
        "apps": app_breakdown([d], rate, hour=h),
        "series": [{"t": r["ts"].strftime("%H:%M:%S"), "w": round(r["wall"], 1),
                    "g": round(r["gpu"], 1), "c": round(r["cpu"], 1)}
                   for r in rows[::step]],
    }


def api_hours(qs):
    """Every hour on record, filtered. This is the searchable history."""
    c = cfg()
    rate = float(c["tariff_jd_per_kwh"])
    rows = all_power_rows()

    buckets = {}
    for r in rows:
        k = (r["ts"].date(), r["ts"].hour)
        b = buckets.setdefault(k, {"wh": 0.0, "sec": 0.0, "peak": 0.0,
                                   "peak_ts": None, "min": None, "n": 0})
        b["wh"] += r["wh"]
        if r["wall"] > 0:
            b["sec"] += r["wh"] / r["wall"] * 3600.0
        if r["wall"] > b["peak"]:
            b["peak"], b["peak_ts"] = r["wall"], r["ts"]
        if b["min"] is None or r["wall"] < b["min"]:
            b["min"] = r["wall"]
        b["n"] += 1

    apps_idx = hour_app_index()

    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return fallback

    d_from = parse_date(qs.get("from"), None)
    d_to = parse_date(qs.get("to"), None)
    try:
        h_from = max(0, min(23, int(qs.get("hfrom", 0))))
        h_to = max(0, min(23, int(qs.get("hto", 23))))
    except ValueError:
        h_from, h_to = 0, 23
    try:
        min_kwh = float(qs.get("min_kwh", 0) or 0)
    except ValueError:
        min_kwh = 0.0
    app_filter = (qs.get("app") or "").strip()
    day_type = qs.get("daytype", "all")
    sort = qs.get("sort", "time")

    out = []
    for (d, h), b in buckets.items():
        if d_from and d < d_from:
            continue
        if d_to and d > d_to:
            continue
        if not (h_from <= h <= h_to):
            continue
        weekend = d.weekday() >= 5
        if day_type == "week" and weekend:
            continue
        if day_type == "weekend" and not weekend:
            continue
        kwh = b["wh"] / 1000.0
        slot = apps_idx.get((d, h), {})
        real = {k: v for k, v in slot.items() if k != BASELINE and v > 0}
        total_slot = sum(slot.values()) or 1.0
        top = max(real.items(), key=lambda kv: kv[1]) if real else None

        picked = None
        if app_filter:
            if app_filter == BASELINE:
                if BASELINE not in slot:
                    continue
            else:
                wh_app = sum(v for k, v in real.items()
                             if friendly(k) == app_filter or k == app_filter)
                # A process that merely existed is not "running": below half a
                # watt-hour it rounds to nothing and is only noise in the list.
                floor_wh = max(min_kwh * 1000.0, 0.5)
                if wh_app < floor_wh:
                    continue
                picked = {"app": app_filter,
                          "kwh": round(wh_app / 1000.0, 5),
                          "cost": round(wh_app / 1000.0 * rate, 5),
                          "share": round(wh_app / total_slot, 4)}
        elif kwh < min_kwh:
            # With no app picked, the threshold applies to the whole hour.
            continue

        out.append({
            "date": d.isoformat(), "hour": h, "weekday": d.strftime("%a"),
            "weekend": weekend,
            "label": "%02d:00" % h,
            "kwh": round(kwh, 5),
            "cost": round(kwh * rate, 5),
            "avg_w": round(b["wh"] / (b["sec"] / 3600.0), 1) if b["sec"] > 0 else 0.0,
            "peak_w": round(b["peak"], 1),
            "peak_at": b["peak_ts"].strftime("%H:%M") if b["peak_ts"] else None,
            "min_w": round(b["min"], 1) if b["min"] is not None else 0.0,
            "coverage": round(min(1.0, b["sec"] / 3600.0), 3),
            "samples": b["n"],
            "top_app": friendly(top[0]) if top else None,
            "top_share": round(top[1] / total_slot, 3) if top else 0,
            "match": picked,
        })

    keys = {"time": lambda x: (x["date"], x["hour"]),
            "kwh": lambda x: -x["kwh"],
            "peak": lambda x: -x["peak_w"],
            "avg": lambda x: -x["avg_w"]}
    out.sort(key=keys.get(sort, keys["time"]))

    tot = sum(x["kwh"] for x in out)
    known = sorted({friendly(a) for slot in apps_idx.values() for a in slot
                    if a != BASELINE})
    dates = sorted({d.isoformat() for (d, _h) in buckets})
    return {
        "hours": out[:2000],
        "currency": c.get("currency", "JOD"), "rate": rate,
        "known_apps": known,
        "first_day": dates[0] if dates else None,
        "last_day": dates[-1] if dates else None,
        "totals": {"hours": len(out), "kwh": round(tot, 3),
                   "cost": round(tot * rate, 3),
                   "avg_kwh": round(tot / len(out), 4) if out else 0,
                   "busiest": max(out, key=lambda x: x["kwh"])["kwh"] if out else 0},
    }


def api_apps(rng):
    c = cfg()
    rate = float(c["tariff_jd_per_kwh"])
    today = datetime.now().date()
    if rng == "today":
        days = [today]
    elif rng == "7d":
        days = [today - timedelta(days=i) for i in range(7)]
    elif rng == "30d":
        days = [today - timedelta(days=i) for i in range(30)]
    else:
        days = []
        for k in app_day_keys():
            try:
                days.append(datetime.strptime(k, "%Y-%m-%d").date())
            except ValueError:
                pass
    apps = app_breakdown(days, rate)
    return {"range": rng, "apps": apps, "rate": rate,
            "currency": c.get("currency", "JOD"),
            "total_kwh": round(sum(a["kwh"] for a in apps), 3),
            "total_cost": round(sum(a["cost"] for a in apps), 2),
            "days": len(days)}


# --------------------------------------------------------------------------
# Who is allowed to read your history. The socket stays dual-stack either way
# -- binding 127.0.0.1 alone would bring back the two-second ::1 stall that
# DualStackServer exists to avoid -- so "localhost" is enforced on the peer
# address instead of at the bind.
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _allowed(peer):
    if str(cfg().get("bind", "lan")).lower() != "localhost":
        return True
    return peer in LOOPBACK or peer.startswith("127.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        if not _allowed(self.client_address[0]):
            self._send(403, "Nabatt is set to localhost only "
                            '(bind: "localhost" in config.json).', "text/plain")
            return
        raw = self.path.split("?", 1)
        path = raw[0]
        # Proper decoding matters: app names carry spaces and brackets, e.g.
        # "llama.cpp server (local LLM)".
        qs = {k: v[0] for k, v in
              urllib.parse.parse_qs(raw[1] if len(raw) > 1 else "",
                                    keep_blank_values=True).items()}
        try:
            if path == "/api/data":
                self._json(api_data())
            elif path == "/api/history":
                self._json(api_history())
            elif path == "/api/day":
                self._json(api_day(qs.get("d", datetime.now().strftime("%Y-%m-%d"))))
            elif path == "/api/hour":
                self._json(api_hour(qs.get("d", datetime.now().strftime("%Y-%m-%d")),
                                    qs.get("h", 0)))
            elif path == "/api/hours":
                self._json(api_hours(qs))
            elif path == "/api/apps":
                self._json(api_apps(qs.get("range", "today")))
            elif path == "/api/health":
                self._json({"ok": True})
            elif path in ("/", "/index.html"):
                with open(os.path.join(WEB, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/icon.png" and os.path.exists(os.path.join(WEB, "icon.png")):
                with open(os.path.join(WEB, "icon.png"), "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, "not found", "text/plain")
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass


class DualStackServer(ThreadingHTTPServer):
    """Listens on IPv6 and IPv4 at once.

    On Windows "localhost" resolves to ::1 before 127.0.0.1. An IPv4-only
    socket therefore makes every client stall on a dead IPv6 connect (~2 s)
    before falling back -- which is slow enough to look like an outage.
    """
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        return ThreadingHTTPServer.server_bind(self)


def serve(port):
    try:
        return DualStackServer(("::", port), Handler)
    except OSError:
        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)   # no IPv6 stack
        srv.daemon_threads = True
        return srv


if __name__ == "__main__":
    port = int(cfg().get("port", 8099))
    for i, a in enumerate(sys.argv):            # --port 9000, for a demo that
        if a == "--port" and i + 1 < len(sys.argv):   # must not collide with
            port = int(sys.argv[i + 1])               # an installed copy
    srv = serve(port)
    # Under pythonw.exe there is no console: sys.stdout is None, and an
    # unguarded print() would take the whole service down at startup.
    if sys.stdout is not None:
        if DEMO:
            print("DEMO MODE - invented data, nothing here was measured")
            print("  %s" % LOGS)
            print("  %d power samples, %d per-app samples" %
                  (_n_power, _n_apps))
        print("dashboard on http://localhost:%d  (and your LAN IP)" % port)
        sys.stdout.flush()
    srv.serve_forever()
