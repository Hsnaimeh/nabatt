"""Where Nabatt keeps its program files and where it keeps your data.

Installed:  program in %LOCALAPPDATA%\\Programs\\Nabatt
            data    in %LOCALAPPDATA%\\Nabatt   (config.json + logs)

Portable:   a file named portable.txt beside the scripts keeps everything --
            config and logs included -- in the app folder instead.

Mirrors paths.ps1; the two must agree.
"""
import json
import os
import shutil

APP = os.path.dirname(os.path.abspath(__file__))

if os.path.exists(os.path.join(APP, "portable.txt")):
    DATA = APP
else:
    DATA = os.path.join(os.environ.get("LOCALAPPDATA") or APP, "Nabatt")

os.makedirs(DATA, exist_ok=True)

LOGS = os.path.join(DATA, "logs")
os.makedirs(LOGS, exist_ok=True)

WEB = os.path.join(APP, "web")

# Your settings live with your data; the copy beside the scripts is the default
# used to seed yours the first time.
CONFIG = os.path.join(DATA, "config.json")
if not os.path.exists(CONFIG):
    seed = os.path.join(APP, "config.json")
    if os.path.exists(seed):
        shutil.copyfile(seed, CONFIG)
    else:
        CONFIG = seed


def cfg():
    with open(CONFIG, encoding="utf-8-sig") as f:
        return json.load(f)
