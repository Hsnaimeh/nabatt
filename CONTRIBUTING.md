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
