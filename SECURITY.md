# Security

## What Nabatt exposes

Nabatt runs a small HTTP service on port 8099. **It has no authentication.**
By default it listens on every interface, so anyone who can reach your machine
on that port — anyone on the same Wi-Fi, typically — can read your history
without being asked for anything.

That is a deliberate feature: it is how you open the dashboard on your phone.
It is also worth understanding before you leave it on, because the history is
more revealing than it first looks. It shows:

- when your machine was switched on and off, to the minute, going back as far
  as your logs do
- which applications you run, and when
- your electricity tariff, and what you have spent

None of that leaves your machine on its own — there is no telemetry, no cloud
account, nothing phones home — but on an untrusted network it is readable by
anyone who goes looking.

## Restricting it to this machine

In `%LOCALAPPDATA%\Nabatt\config.json`:

```json
"bind": "localhost"
```

Then restart: `stop-power-tracking.ps1`, `start-power-tracking.ps1`. Requests
from anywhere but the loopback address are refused with `403`. The desktop app,
the tray icon and everything else keep working; only other devices lose access.

The socket itself stays dual-stack in both modes — binding `127.0.0.1` alone
would reintroduce a two-second stall on every `localhost` request, because
Windows resolves `localhost` to `::1` first. The restriction is applied to the
peer address instead.

Use `"bind": "localhost"` on any network you do not control. Cafés, hotels,
co-working spaces, conference Wi-Fi.

## Reporting something

Open an issue if it is not sensitive. If it is, email
**h.snaimhe93@gmail.com** rather than filing publicly, and give it a few days
before disclosing.

This is a personal project maintained in spare time. There is no SLA, and no
bounty.

## What is out of scope

- **The dashboard being readable on your own LAN by default.** Documented
  above, switchable in one line.
- **Plain-text CSV logs in your own profile.** Anyone who can read
  `%LOCALAPPDATA%\Nabatt\logs` is already logged in as you.
- **The installer writing to HKCU and `%LOCALAPPDATA%`.** That is what a
  per-user install is; it needs no admin rights and touches nothing shared.
