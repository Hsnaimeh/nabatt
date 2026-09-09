# Contributing to Nabatt

Contributions are welcome — bug reports especially. Nabatt has so far run on
exactly one machine, so almost every assumption in it is worth challenging.

## What would help most

**Other hardware.** The power model was calibrated against one RTX 3090 and one
32-thread CPU. If your numbers look wrong, that is a finding, not a support
question — open an issue with your GPU, CPU, and what Nabatt reported next to
what your wall meter or PSU says.

**AMD and Intel GPUs.** GPU watts currently come from `nvidia-smi`. The
per-process side already uses Windows' own `\GPU Engine(pid_*)` counters, which
are vendor-neutral, so most of the work is reading the card's own sensor.

**Time and locale.** Dates are parsed and formatted in a small number of
places; anything that assumes a Gregorian, English, left-to-right context is a
fair thing to fix.

**Accuracy over polish.** A change that makes a number more honest is worth more
here than one that makes it prettier.

## Ground rules for the numbers

Nabatt's one real promise is that it does not invent data. Please keep it:

- Time the logger did not observe is a **gap**, reported as a gap. Never
  interpolate across it, never extrapolate an average over it.
- Power that cannot be attributed to a process stays in `__baseline__`. Do not
  redistribute it to make app shares add up to the wall figure.
- A modelled number is labelled as modelled. Right now CPU watts are modelled
  and GPU watts are measured, and the README says so.

If a change breaks one of these, it needs a very good reason in the pull
request.

## Working on the dashboard without Windows

```bash
python dashboard.py --demo
```

That generates a fortnight of synthetic samples into a temp folder and serves
them through the normal code path, so the whole UI, every endpoint and all of
the arithmetic can be worked on from Linux or macOS with no GPU. `demo_data.py`
writes the CSVs; it is also the obvious fixture to build a test suite on, since
it produces known input for maths that is otherwise awkward to exercise.

If you change the log format, change `demo_data.py` in the same commit — it is
the only executable description of that format outside the loggers themselves.

## Running it from source

```powershell
git clone https://github.com/Hsnaimeh/nabatt.git
cd nabatt
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Python 3 must be on `PATH`. `install.ps1` fetches `pystray` and `pillow` for the
tray icon; everything else is standard library and PowerShell 5.1.

To work on it without touching your real history, drop an empty `portable.txt`
next to the scripts — config and logs then stay in the working folder instead of
`%LOCALAPPDATA%\Nabatt`.

`stop-power-tracking.ps1` and `start-power-tracking.ps1` restart the four
background processes after a change. The dashboard is plain Python with no
build step, and `web/index.html` is a single file with no dependencies and no
bundler — edit it and reload the page.

## Tests

```bash
python -m unittest discover -s tests -v
```

Standard library, no hardware, under a second. They run anywhere `dashboard.py`
runs, Linux and macOS included.

They deliberately concentrate on the promises above rather than on coverage:
that a gap stays a gap, that an average is taken over observed time and not
wall-clock, that a single missed sample is *not* reported as a gap while a real
pause is, that app shares sum to one with the baseline kept separate, and that
a torn CSV line is skipped instead of taking the dashboard down. One test is
named for a bug that actually shipped — a short-lived process reporting 0.0 W
beside a real peak — so it cannot come back quietly.

If you change any of that behaviour on purpose, change the test in the same
commit and say why in the message.

## Style

Match what is already there. PowerShell 5.1 compatible (no `??`, no ternaries),
Python standard library only in `dashboard.py`, no front-end framework, and
comments that explain *why* rather than restate the line below them.

## Reporting a bug

Include your Windows version, GPU, what you expected, and what you got. If it
concerns a specific reading, the relevant few lines of
`%LOCALAPPDATA%\Nabatt\logs\power-YYYY-MM.csv` are usually enough — but read
them first, since they record when your machine was on.

## Licence

By contributing you agree that your work is licensed under the
[MIT License](LICENSE).
