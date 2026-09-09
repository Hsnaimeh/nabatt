"""Synthetic logs, so the dashboard can be explored without the hardware.

Nabatt normally needs Windows, an NVIDIA card and a couple of days of patience
before there is anything to look at. `python dashboard.py --demo` skips all
three: this module writes a plausible fortnight of samples into a temporary
folder, in exactly the CSV format the real loggers produce, and the dashboard
reads them the way it reads anything else. No part of the app is stubbed out.

The data is invented. It is shaped like a real working machine -- on in the
morning, off at night, quiet weekends, a few heavy GPU sessions -- but nothing
here was measured, and the dashboard says so in the header.
"""
import csv
import json
import math
import os
import random
import shutil
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))

POWER_HEADER = ['ts', 'epoch', 'dt_s', 'gpu_w', 'gpu_util', 'cpu_util',
                'cpu_w', 'rest_w', 'wall_w', 'wh', 'src']
APPS_HEADER = ['ts', 'app', 'pids', 'gpu_pct', 'cpu_pct', 'gpu_w', 'cpu_w',
               'watts', 'wh']

POWER_DAYS = 14        # whole-machine history
APP_DAYS = 7           # per-app history starts later, as it did in reality
SAMPLE_S = 10
APP_SAMPLE_S = 30

# What the machine is doing, and what that costs. Each block is
# (label, minutes_range, gpu_util_range, cpu_util_range, apps) where apps maps
# a process name to its rough share of the work above idle.
BLOCKS = [
    ('idle', (10, 45), (0, 2), (1, 6), {
        'chrome': .35, 'MsMpEng': .2, 'dwm': .15, 'explorer': .1,
        'svchost': .1, 'WmiPrvSE': .1}),
    ('browsing', (15, 50), (2, 14), (5, 18), {
        'chrome': .55, 'msedge': .12, 'dwm': .12, 'Code': .1, 'svchost': .06,
        'WmiPrvSE': .05}),
    ('coding', (20, 70), (1, 8), (12, 38), {
        'Code': .4, 'node': .22, 'chrome': .18, 'python': .1, 'dwm': .05,
        'svchost': .05}),
    ('build', (4, 14), (0, 5), (55, 96), {
        'node': .45, 'python': .3, 'Code': .15, 'svchost': .1}),
    ('llm', (25, 95), (62, 99), (10, 30), {
        'llama-server': .82, 'python': .07, 'WindowsTerminal': .05,
        'chrome': .04, 'dwm': .02}),
    ('video', (12, 40), (35, 78), (40, 85), {
        'ffmpeg': .62, 'MediaSDKApplication': .2, 'chrome': .1, 'dwm': .08}),
    ('meeting', (25, 60), (6, 20), (14, 32), {
        'Teams': .45, 'chrome': .25, 'dwm': .15, 'msedge': .15}),
]
WEIGHTS_WEEKDAY = [3, 4, 4, 1, 3, 1, 2]
WEIGHTS_WEEKEND = [5, 4, 2, 0, 2, 2, 0]


def _cfg():
    with open(os.path.join(HERE, 'config.json'), encoding='utf-8-sig') as f:
        return json.load(f)


def _cpu_watts(util, c):
    lo = float(c['cpu_idle_w'])
    hi = float(c['cpu_max_w'])
    return lo + (hi - lo) * (max(0.0, util) / 100.0) ** float(c['cpu_curve_exp'])


def _gpu_watts(util, idle):
    # an RTX-class card: idles low, climbs steeply, tops out around 350 W
    return idle + (350.0 - idle) * (max(0.0, util) / 100.0) ** 1.15


def _hours(t):
    """Time of day as a float number of hours."""
    return t.hour + t.minute / 60.0 + t.second / 3600.0


def _sessions(day, rnd):
    """When the machine was switched on, on this date."""
    weekend = day.weekday() >= 5
    if weekend and rnd.random() < 0.35:
        return []                                    # never turned it on
    out = []
    if weekend:
        start = rnd.uniform(10.5, 13.0)
        out.append((start, start + rnd.uniform(2.0, 5.0)))
        if rnd.random() < 0.45:
            ev = rnd.uniform(19.0, 21.0)
            out.append((ev, ev + rnd.uniform(1.0, 3.0)))
    else:
        start = rnd.uniform(7.7, 9.4)
        lunch = rnd.uniform(12.5, 13.5)
        out.append((start, lunch))                   # morning
        back = lunch + rnd.uniform(0.4, 1.1)         # a real gap at lunch
        out.append((back, back + rnd.uniform(3.5, 5.5)))
        if rnd.random() < 0.4:
            ev = rnd.uniform(20.0, 22.0)
            out.append((ev, ev + rnd.uniform(1.0, 2.5)))
    return [(a, min(b, 23.99)) for a, b in out if b > a]


def _timeline(t0, t1, rnd, weekend):
    """A sequence of (block, start, end) filling one session."""
    weights = WEIGHTS_WEEKEND if weekend else WEIGHTS_WEEKDAY
    out = []
    t = t0
    while t < t1:
        b = rnd.choices(BLOCKS, weights=weights)[0]
        dur = timedelta(minutes=rnd.uniform(*b[1]))
        end = min(t + dur, t1)
        out.append((b, t, end))
        t = end
    return out


def generate(logs_dir, seed=20260908):
    """Write power-YYYY-MM.csv and apps-YYYY-MM-DD.csv into logs_dir."""
    rnd = random.Random(seed)
    c = _cfg()
    gpu_idle = float(c.get('gpu_idle_w', 40))
    rest_w = float(c.get('rest_of_system_w', 60))
    psu = float(c.get('psu_efficiency', 0.9))

    today = datetime.now().replace(microsecond=0)
    months = {}                                   # 'YYYY-MM' -> list of rows
    app_days = {}                                 # 'YYYY-MM-DD' -> list of rows

    for back in range(POWER_DAYS - 1, -1, -1):
        day = (today - timedelta(days=back)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        weekend = day.weekday() >= 5
        wants_apps = back < APP_DAYS

        spans = _sessions(day, rnd)
        if back == 0:
            # Today is only half-lived. Clip it to now, and make sure the last
            # session runs right up to now -- otherwise the Live tab opens on
            # an empty machine, which is not what a demo should show.
            spans = [(a, min(b, _hours(today))) for a, b in spans]
            spans = [(a, b) for a, b in spans if b > a]
            if not spans or _hours(today) - spans[-1][1] > 0.05:
                spans.append((max(0.0, _hours(today) - rnd.uniform(2.0, 3.5)),
                              _hours(today)))

        for s0, s1 in spans:
            t0 = day + timedelta(hours=s0)
            t1 = min(day + timedelta(hours=s1), today if back == 0 else day +
                     timedelta(days=1))
            if t1 <= t0:
                continue

            next_app_at = t0
            for block, b0, b1 in _timeline(t0, t1, rnd, weekend):
                label, _, gpu_r, cpu_r, mix = block
                t = b0
                while t < b1:
                    # a little noise on top of the block's baseline load
                    phase = (t - b0).total_seconds() / 60.0
                    wobble = math.sin(phase * 1.7) * 0.18 + rnd.uniform(-.12, .12)
                    gu = min(100.0, max(0.0, rnd.uniform(*gpu_r) * (1 + wobble)))
                    cu = min(100.0, max(0.0, rnd.uniform(*cpu_r) * (1 + wobble)))
                    gw = _gpu_watts(gu, gpu_idle)
                    cw = _cpu_watts(cu, c)
                    wall = (gw + cw + rest_w) / psu
                    months.setdefault(t.strftime('%Y-%m'), []).append([
                        t.strftime('%Y-%m-%d %H:%M:%S'), int(t.timestamp()),
                        '%.1f' % SAMPLE_S, '%.1f' % gw, int(round(gu)),
                        int(round(cu)), '%.1f' % cw, '%.1f' % rest_w,
                        '%.1f' % wall, '%.5f' % (wall * SAMPLE_S / 3600.0),
                        'live'])

                    if wants_apps and t >= next_app_at:
                        _app_sample(app_days, t, gw, cw, wall, gu, cu, mix,
                                    gpu_idle, c, rnd)
                        next_app_at = t + timedelta(seconds=APP_SAMPLE_S)
                    t += timedelta(seconds=SAMPLE_S)

    os.makedirs(logs_dir, exist_ok=True)
    for key, rows in months.items():
        with open(os.path.join(logs_dir, 'power-%s.csv' % key), 'w',
                  newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(POWER_HEADER)
            w.writerows(rows)
    for key, rows in app_days.items():
        with open(os.path.join(logs_dir, 'apps-%s.csv' % key), 'w',
                  newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(APPS_HEADER)
            w.writerows(rows)
    return sum(len(v) for v in months.values()), sum(
        len(v) for v in app_days.values())


def _app_sample(app_days, t, gw, cw, wall, gu, cu, mix, gpu_idle, c, rnd):
    """One per-application sample, split the way app-logger.ps1 splits it."""
    key = t.strftime('%Y-%m-%d')
    stamp = t.strftime('%Y-%m-%d %H:%M:%S')
    rows = app_days.setdefault(key, [])

    # only the power above the two idle floors is ever charged to an app
    gpu_pool = max(0.0, gw - gpu_idle)
    cpu_pool = max(0.0, cw - float(c['cpu_idle_w']))
    baseline = wall - gpu_pool - cpu_pool

    for app, share in mix.items():
        s = max(0.0, share * rnd.uniform(0.82, 1.18))
        g = gpu_pool * s
        p = cpu_pool * s
        watts = g + p
        if watts < 0.02:
            continue
        rows.append([stamp, app, rnd.randint(1, 3),
                     '%.2f' % (gu * s), '%.2f' % (cu * s),
                     '%.2f' % g, '%.2f' % p, '%.2f' % watts,
                     '%.5f' % (watts * APP_SAMPLE_S / 3600.0)])
    rows.append([stamp, '__baseline__', 0, 0, 0, 0, '%.2f' % baseline,
                 '%.2f' % baseline,
                 '%.5f' % (baseline * APP_SAMPLE_S / 3600.0)])


def setup():
    """Build a demo log folder and the config to read it with."""
    logs = os.path.join(tempfile.gettempdir(), 'nabatt-demo-logs')
    shutil.rmtree(logs, ignore_errors=True)
    n_power, n_apps = generate(logs)
    c = _cfg()
    c['billing_start'] = ''            # nothing to estimate before the samples
    return logs, c, n_power, n_apps


if __name__ == '__main__':
    logs, _, a, b = setup()
    print('%s\n  %d power samples, %d per-app samples' % (logs, a, b))
