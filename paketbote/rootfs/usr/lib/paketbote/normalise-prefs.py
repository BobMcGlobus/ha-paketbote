#!/usr/bin/env python3
"""Take the parts of Chrome's saved preferences that get in our way.

Chrome stores its window geometry in the profile and restores it on start,
which overrides --window-position and --window-size. A placement saved while
the screen was a different size — or while something moved the window — is then
carried forward forever and the browser comes up somewhere invisible. Dropping
the saved placement makes the command line apply again.

The same pass clears the "didn't shut down correctly" flags. That used to be a
sed over JSON, which worked right up until it didn't.

Usage: normalise-prefs.py <path to Default/Preferences>
Exits 0 whatever happens: a failed s6 oneshot stops the whole container.
"""

from __future__ import annotations

import json
import sys


def main(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as handle:
            prefs = json.load(handle)
    except (OSError, ValueError) as err:
        print(f"preferences not readable ({err}); leaving them alone")
        return 0

    if not isinstance(prefs, dict):
        return 0

    changed = []

    browser = prefs.get("browser")
    if isinstance(browser, dict) and "window_placement" in browser:
        del browser["window_placement"]
        changed.append("window placement")

    profile = prefs.get("profile")
    if isinstance(profile, dict):
        if profile.get("exit_type") != "Normal":
            profile["exit_type"] = "Normal"
            changed.append("exit type")
        if profile.get("exited_cleanly") is False:
            profile["exited_cleanly"] = True
            changed.append("clean exit flag")

    if not changed:
        return 0

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(prefs, handle, separators=(",", ":"))
    except OSError as err:
        print(f"could not write preferences ({err})")
        return 0

    print("reset: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: normalise-prefs.py <preferences file>")
        sys.exit(0)
    sys.exit(main(sys.argv[1]))
