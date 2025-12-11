#!/usr/bin/env python3
import curses
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import subprocess

APP_NAME = "iwctl-helper"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"

# ---- ANSI stripping (for iwctl colored output) ----
ANSI_ESCAPE_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

def strip_ansi(s: str) -> str:
    return ANSI_ESCAPE_RE.sub('', s)


@dataclass
class AppState:
    station: Optional[str] = None  # used as <wlan>
    adapter: Optional[str] = None  # used as <phy>

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump({"station": self.station, "adapter": self.adapter}, f, indent=2)

    @classmethod
    def load(cls) -> "AppState":
        if CONFIG_PATH.exists():
            try:
                with CONFIG_PATH.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls(
                    station=data.get("station"),
                    adapter=data.get("adapter"),
                )
            except Exception:
                return cls()
        return cls()


# ---------- shell helpers ----------

def run_iwctl(args):
    """
    Run iwctl and return (stdout, stderr, returncode).
    """
    try:
        proc = subprocess.run(
            ["iwctl"] + args,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except FileNotFoundError:
        return "", "iwctl not found in PATH. Please install iwd / iwctl.", 127


def first_run_setup(state: AppState) -> AppState:
    """
    Show `iwctl device list` and ask for default station/adapter on first run.
    This is done outside curses.
    """
    if state.station and state.adapter:
        return state

    print("First run setup for iwctl TUI helper")
    print("------------------------------------")
    print("Running: iwctl device list\n")

    out, err, rc = run_iwctl(["device", "list"])
    if out:
        print(out)
    if err:
        print(err)
    if rc != 0:
        print(f"\nWARNING: iwctl returned exit code {rc}\n")

    print("Please configure defaults used by the TUI.")
    station = input("Default station (wlan, e.g. wlan0): ").strip()
    adapter = input("Default adapter (phy, e.g. phy0 or wlan0): ").strip()

    if station:
        state.station = station
    if adapter:
        state.adapter = adapter

    state.save()
    print("\nDefaults saved. Launching TUI.")
    input("Press Enter to continue...")
    return state


# ---------- curses helpers ----------

def draw_centered(stdscr, y, text, attr=0):
    h, w = stdscr.getmaxyx()
    x = max(0, (w - len(text)) // 2)
    stdscr.addstr(y, x, text[: max(w - x - 1, 0)], attr)


def input_curses(stdscr, prompt: str, initial: str = "") -> str:
    """
    Simple line input in curses; supports spaces.
    """
    curses.echo()
    stdscr.clear()
    stdscr.addstr(0, 0, prompt)
    stdscr.addstr(1, 0, initial)
    stdscr.move(1, len(initial))
    stdscr.refresh()
    value = stdscr.getstr(1, len(initial)).decode("utf-8", errors="ignore").strip()
    curses.noecho()
    return value


def show_output_screen(stdscr, title: str, command: str, output: str, error: str):
    # strip iwctl ANSI color codes so they don't leak as ^[[0m, etc.
    output = strip_ansi(output or "")
    error = strip_ansi(error or "")

    stdscr.clear()
    h, w = stdscr.getmaxyx()

    stdscr.addstr(0, 0, title[: w - 1], curses.A_BOLD)
    cmd_line = f"$ {command}"
    if len(cmd_line) >= w:
        cmd_line = cmd_line[: w - 4] + "..."
    stdscr.addstr(1, 0, cmd_line, curses.A_DIM)

    lines = []
    if output:
        lines.extend(output.splitlines())
    if error:
        if lines:
            lines.append("")
        lines.append("stderr:")
        lines.extend(error.splitlines())

    max_lines = h - 4
    for i, line in enumerate(lines[:max_lines]):
        stdscr.addstr(3 + i, 0, line[: w - 1])

    if len(lines) > max_lines:
        stdscr.addstr(
            h - 2,
            0,
            f"... output truncated ({len(lines) - max_lines} more lines) ...",
        )

    stdscr.addstr(h - 1, 0, "Press any key to go back...", curses.A_REVERSE)
    stdscr.refresh()
    stdscr.getch()


def generic_menu(stdscr, title: str, items: list[str], start_index: int = 0) -> int:
    current = start_index
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        draw_centered(stdscr, 0, title, curses.A_BOLD | curses.A_UNDERLINE)

        for idx, item in enumerate(items):
            y = 2 + idx
            label = f"{idx + 1}) {item}"
            if idx == current:
                # use STANDOUT so it follows the user's theme
                stdscr.addstr(y, 2, label[: w - 3], curses.A_STANDOUT)
            else:
                stdscr.addstr(y, 2, label[: w - 3])

        stdscr.addstr(h - 1, 0, "↑/↓ to navigate, Enter to select", curses.A_DIM)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            current = (current - 1) % len(items)
        elif key in (curses.KEY_DOWN, ord("j")):
            current = (current + 1) % len(items)
        elif key in (curses.KEY_ENTER, 10, 13):
            return current
        elif key in (ord("q"), 27):  # q or ESC = last option (Back/Quit)
            return len(items) - 1


# ---------- small helpers for wlan/adapter ----------

def ask_wlan(stdscr, state: AppState, action: str) -> Optional[str]:
    default = state.station or ""
    prompt = (
        f"wlan for {action} (current default: {state.station!r}) "
        "[Enter = use default]: "
    )
    sel = input_curses(stdscr, prompt, "")
    if not sel:
        sel = default
    if not sel:
        show_output_screen(
            stdscr,
            "Error",
            "N/A",
            "",
            "No wlan specified and no default station configured.",
        )
        return None
    return sel


def ask_adapter(stdscr, state: AppState, action: str) -> Optional[str]:
    default = state.adapter or ""
    prompt = (
        f"Adapter/phy for {action} (current default: {state.adapter!r}) "
        "[Enter = use default]: "
    )
    sel = input_curses(stdscr, prompt, "")
    if not sel:
        sel = default
    if not sel:
        show_output_screen(
            stdscr,
            "Error",
            "N/A",
            "",
            "No adapter specified and no default adapter configured.",
        )
        return None
    return sel


# ---------- Submenus (unchanged logic) ----------

# Adapters:
def submenu_adapters(stdscr, state: AppState):
    items = [
        "List adapters",
        "Show adapter info",
        "Set adapter property",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL ADAPTERS SUBMENU", items)

        if choice == 0:  # adapter list
            args = ["adapter", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr,
                "Adapters - List",
                "iwctl " + " ".join(args),
                out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 1:  # adapter <phy> show
            phy = ask_adapter(stdscr, state, "show")
            if not phy:
                continue
            args = ["adapter", phy, "show"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr,
                "Adapters - Show",
                "iwctl " + " ".join(args),
                out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 2:  # adapter <phy> set-property <name> <value>
            phy = ask_adapter(stdscr, state, "set-property")
            if not phy:
                continue
            prop_name = input_curses(stdscr, "Property name (e.g. Powered): ")
            if not prop_name:
                show_output_screen(stdscr, "Error", "N/A", "", "No property name.")
                continue
            prop_val = input_curses(
                stdscr, f"Property value for {prop_name} (e.g. on/off): "
            )
            if not prop_val:
                show_output_screen(stdscr, "Error", "N/A", "", "No property value.")
                continue
            args = ["adapter", phy, "set-property", prop_name, prop_val]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr,
                "Adapters - Set property",
                "iwctl " + " ".join(args),
                out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 3:
            return


# Ad-Hoc:
def submenu_ad_hoc(stdscr, state: AppState):
    items = [
        "List Ad-Hoc devices",
        "Start Ad-Hoc network",
        "Start open Ad-Hoc network",
        "Stop Ad-Hoc on wlan",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL AD-HOC SUBMENU", items)

        if choice == 0:  # ad-hoc list
            args = ["ad-hoc", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr,
                "Ad-Hoc - List",
                "iwctl " + " ".join(args),
                out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 1:  # ad-hoc <wlan> start "name" pass
            wlan = ask_wlan(stdscr, state, "Ad-Hoc start")
            if not wlan:
                continue
            name = input_curses(
                stdscr,
                'Network name (SSID, can contain spaces; no quotes needed): ',
            )
            if not name:
                show_output_screen(stdscr, "Error", "N/A", "", "No network name.")
                continue
            pw = input_curses(stdscr, "Passphrase: ")
            if not pw:
                show_output_screen(stdscr, "Error", "N/A", "", "No passphrase.")
                continue
            args = ["ad-hoc", wlan, "start", name, pw]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr,
                "Ad-Hoc - Start",
                "iwctl " + " ".join(args),
                out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 2:  # ad-hoc <wlan> start_open "name"
            wlan = ask_wlan(stdscr, state, "Ad-Hoc start_open")
            if not wlan:
                continue
            name = input_curses(
                stdscr,
                'Open Ad-Hoc network name (SSID): ',
            )
            if not name:
                show_output_screen(stdscr, "Error", "N/A", "", "No network name.")
                continue
            args = ["ad-hoc", wlan, "start_open", name]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr,
                "Ad-Hoc - Start open",
                "iwctl " + " ".join(args),
                out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 3:  # ad-hoc <wlan> stop
            wlan = ask_wlan(stdscr, state, "Ad-Hoc stop")
            if not wlan:
                continue
            args = ["ad-hoc", wlan, "stop"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr,
                "Ad-Hoc - Stop",
                "iwctl " + " ".join(args),
                out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 4:
            return


# Access Point:
def submenu_access_point(stdscr, state: AppState):
    items = [
        "List AP-mode devices",
        "Start access point",
        "Start access point from profile",
        "Stop access point",
        "Show AP info",
        "Scan (AP)",
        "Get AP networks",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL AP SUBMENU", items)

        if choice == 0:  # ap list
            args = ["ap", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "AP - List", "iwctl " + " ".join(args), out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 1:  # ap <wlan> start "name" pass
            wlan = ask_wlan(stdscr, state, "AP start")
            if not wlan:
                continue
            name = input_curses(stdscr, 'AP network name (SSID): ')
            if not name:
                show_output_screen(stdscr, "Error", "N/A", "", "No network name.")
                continue
            pw = input_curses(stdscr, "Passphrase: ")
            if not pw:
                show_output_screen(stdscr, "Error", "N/A", "", "No passphrase.")
                continue
            args = ["ap", wlan, "start", name, pw]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "AP - Start", "iwctl " + " ".join(args), out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 2:  # ap <wlan> start-profile "name"
            wlan = ask_wlan(stdscr, state, "AP start-profile")
            if not wlan:
                continue
            name = input_curses(
                stdscr,
                'Profile name / "network name": ',
            )
            if not name:
                show_output_screen(stdscr, "Error", "N/A", "", "No profile name.")
                continue
            args = ["ap", wlan, "start-profile", name]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "AP - Start profile", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 3:  # ap <wlan> stop
            wlan = ask_wlan(stdscr, state, "AP stop")
            if not wlan:
                continue
            args = ["ap", wlan, "stop"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "AP - Stop", "iwctl " + " ".join(args), out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 4:  # ap <wlan> show
            wlan = ask_wlan(stdscr, state, "AP show")
            if not wlan:
                continue
            args = ["ap", wlan, "show"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "AP - Show", "iwctl " + " ".join(args), out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 5:  # ap <wlan> scan
            wlan = ask_wlan(stdscr, state, "AP scan")
            if not wlan:
                continue
            args = ["ap", wlan, "scan"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "AP - Scan", "iwctl " + " ".join(args), out,
                err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 6:  # ap <wlan> get-networks
            wlan = ask_wlan(stdscr, state, "AP get-networks")
            if not wlan:
                continue
            args = ["ap", wlan, "get-networks"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "AP - Get networks", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 7:
            return


# Devices:
def submenu_devices(stdscr, state: AppState):
    items = [
        "List devices",
        "Show device info",
        "Set device property",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL DEVICES SUBMENU", items)

        if choice == 0:  # device list
            args = ["device", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Devices - List", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 1:  # device <wlan> show
            wlan = ask_wlan(stdscr, state, "device show")
            if not wlan:
                continue
            args = ["device", wlan, "show"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Devices - Show", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 2:  # device <wlan> set-property
            wlan = ask_wlan(stdscr, state, "device set-property")
            if not wlan:
                continue
            prop_name = input_curses(stdscr, "Property name: ")
            if not prop_name:
                show_output_screen(stdscr, "Error", "N/A", "", "No property name.")
                continue
            prop_val = input_curses(stdscr, "Property value: ")
            if not prop_val:
                show_output_screen(stdscr, "Error", "N/A", "", "No property value.")
                continue
            args = ["device", wlan, "set-property", prop_name, prop_val]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Devices - Set property", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 3:
            return


# Known Networks:
def submenu_known_networks(stdscr, state: AppState):
    items = [
        "List known networks",
        "Show known network",
        "Forget known network",
        "Set known network property",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL KNOWN NETWORKS SUBMENU", items)

        if choice == 0:  # known-networks list
            args = ["known-networks", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Known Networks - List", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice in (1, 2, 3):
            name = input_curses(
                stdscr,
                'Known network name (as shown in list, may need quotes normally): ',
            )
            if not name:
                show_output_screen(stdscr, "Error", "N/A", "", "No network name.")
                continue

            if choice == 1:  # show
                args = ["known-networks", name, "show"]
                title = "Known Networks - Show"
            elif choice == 2:  # forget
                args = ["known-networks", name, "forget"]
                title = "Known Networks - Forget"
            else:  # set-property
                prop_name = input_curses(stdscr, "Property name: ")
                if not prop_name:
                    show_output_screen(stdscr, "Error", "N/A", "", "No property name.")
                    continue
                prop_val = input_curses(stdscr, "Property value: ")
                if not prop_val:
                    show_output_screen(
                        stdscr, "Error", "N/A", "", "No property value."
                    )
                    continue
                args = ["known-networks", name, "set-property", prop_name, prop_val]
                title = "Known Networks - Set property"

            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, title, "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 4:
            return


# WiFi Simple Configuration (WSC):
def submenu_wsc(stdscr, state: AppState):
    items = [
        "List WSC-capable devices",
        "PushButton mode",
        "Start user PIN mode",
        "Start PIN (generated)",
        "Cancel WSC",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL WSC SUBMENU", items)

        if choice == 0:  # wsc list
            args = ["wsc", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "WSC - List", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice in (1, 2, 3, 4):
            wlan = ask_wlan(stdscr, state, "WSC")
            if not wlan:
                continue

            if choice == 1:  # push-button
                args = ["wsc", wlan, "push-button"]
                title = "WSC - PushButton"
            elif choice == 2:  # start-user-pin <PIN>
                pin = input_curses(stdscr, "PIN (e.g. 12345670): ")
                if not pin:
                    show_output_screen(stdscr, "Error", "N/A", "", "No PIN entered.")
                    continue
                args = ["wsc", wlan, "start-user-pin", pin]
                title = "WSC - Start user PIN"
            elif choice == 3:  # start-pin
                args = ["wsc", wlan, "start-pin"]
                title = "WSC - Start PIN (generated)"
            else:  # cancel
                args = ["wsc", wlan, "cancel"]
                title = "WSC - Cancel"

            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, title, "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 5:
            return


# Station:
def submenu_station(stdscr, state: AppState):
    items = [
        "List station devices",
        "Connect to network",
        "Connect to hidden network",
        "Disconnect",
        "Get networks",
        "Get hidden access points",
        "Scan for networks",
        "Show station info",
        "Get BSSes",
        "Change default station / adapter",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL STATION SUBMENU", items)

        if choice == 0:  # station list
            args = ["station", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - List", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 1:  # connect
            wlan = ask_wlan(stdscr, state, "station connect")
            if not wlan:
                continue
            name = input_curses(
                stdscr,
                "Network name (SSID): ",
            )
            if not name:
                show_output_screen(stdscr, "Error", "N/A", "", "No network name.")
                continue
            security = input_curses(
                stdscr,
                "Security (optional, e.g. psk, leave empty for default): ",
            )
            args = ["station", wlan, "connect", name]
            if security:
                args.append(security)
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Connect", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 2:  # connect-hidden
            wlan = ask_wlan(stdscr, state, "station connect-hidden")
            if not wlan:
                continue
            name = input_curses(stdscr, "Hidden network name (SSID): ")
            if not name:
                show_output_screen(stdscr, "Error", "N/A", "", "No network name.")
                continue
            args = ["station", wlan, "connect-hidden", name]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Connect hidden", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 3:  # disconnect
            wlan = ask_wlan(stdscr, state, "station disconnect")
            if not wlan:
                continue
            args = ["station", wlan, "disconnect"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Disconnect", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 4:  # get-networks [rssi-dbms/rssi-bars]
            wlan = ask_wlan(stdscr, state, "station get-networks")
            if not wlan:
                continue
            mode = input_curses(
                stdscr,
                "Mode (optional: rssi-dbms / rssi-bars, empty for default): ",
            )
            args = ["station", wlan, "get-networks"]
            if mode:
                args.append(mode)
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Get networks", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 5:  # get-hidden-access-points [rssi-dbms]
            wlan = ask_wlan(stdscr, state, "station get-hidden-access-points")
            if not wlan:
                continue
            mode = input_curses(
                stdscr,
                "Mode (optional: rssi-dbms, empty for default): ",
            )
            args = ["station", wlan, "get-hidden-access-points"]
            if mode:
                args.append(mode)
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Get hidden APs", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 6:  # scan
            wlan = ask_wlan(stdscr, state, "station scan")
            if not wlan:
                continue
            args = ["station", wlan, "scan"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Scan", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 7:  # show
            wlan = ask_wlan(stdscr, state, "station show")
            if not wlan:
                continue
            args = ["station", wlan, "show"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Show", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 8:  # get-bsses [network] [security]
            wlan = ask_wlan(stdscr, state, "station get-bsses")
            if not wlan:
                continue
            network = input_curses(
                stdscr,
                "Network (optional SSID, empty = all): ",
            )
            security = input_curses(
                stdscr,
                "Security (optional, e.g. psk, empty = any): ",
            )
            args = ["station", wlan, "get-bsses"]
            if network:
                args.append(network)
            if security:
                args.append(security)
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "Station - Get BSSes", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 9:  # change defaults
            new_station = input_curses(
                stdscr,
                f"New default station (current {state.station!r}, empty = keep): ",
            )
            new_adapter = input_curses(
                stdscr,
                f"New default adapter (current {state.adapter!r}, empty = keep): ",
            )
            if new_station:
                state.station = new_station
            if new_adapter:
                state.adapter = new_adapter
            state.save()
            text = (
                "Defaults updated:\n\n"
                f"  Station: {state.station!r}\n"
                f"  Adapter: {state.adapter!r}\n"
            )
            show_output_screen(stdscr, "Station - Defaults updated", "N/A", text, "")

        elif choice == 10:
            return


# Device Provisioning (DPP):
def submenu_dpp(stdscr, state: AppState):
    items = [
        "List DPP-capable devices",
        "Start DPP Enrollee",
        "Start DPP Configurator",
        "Stop DPP",
        "Show DPP state",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL DPP SUBMENU", items)

        if choice == 0:  # dpp list
            args = ["dpp", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "DPP - List", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice in (1, 2, 3, 4):
            wlan = ask_wlan(stdscr, state, "DPP")
            if not wlan:
                continue

            if choice == 1:
                args = ["dpp", wlan, "start-enrollee"]
                title = "DPP - Start Enrollee"
            elif choice == 2:
                args = ["dpp", wlan, "start-configurator"]
                title = "DPP - Start Configurator"
            elif choice == 3:
                args = ["dpp", wlan, "stop"]
                title = "DPP - Stop"
            else:
                args = ["dpp", wlan, "show"]
                title = "DPP - Show"

            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, title, "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 5:
            return


# PKEX:
def submenu_pkex(stdscr, state: AppState):
    items = [
        "List PKEX-capable devices",
        "Stop PKEX",
        "Show PKEX state",
        "Start PKEX enrollee",
        "Start PKEX configurator",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL PKEX SUBMENU", items)

        if choice == 0:  # pkex list
            args = ["pkex", "list"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "PKEX - List", "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice in (1, 2, 3, 4):
            wlan = ask_wlan(stdscr, state, "PKEX")
            if not wlan:
                continue

            if choice == 1:  # stop
                args = ["pkex", wlan, "stop"]
                title = "PKEX - Stop"
            elif choice == 2:  # show
                args = ["pkex", wlan, "show"]
                title = "PKEX - Show"
            else:
                key = input_curses(stdscr, "Shared code key: ")
                if not key:
                    show_output_screen(stdscr, "Error", "N/A", "", "No key.")
                    continue
                ident = input_curses(
                    stdscr,
                    "Identifier (optional, empty for none): ",
                )
                if choice == 3:  # enroll
                    args = ["pkex", wlan, "enroll", key]
                    title = "PKEX - Enroll"
                else:  # configure
                    args = ["pkex", wlan, "configure", key]
                    title = "PKEX - Configure"
                if ident:
                    args.append(ident)

            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, title, "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 5:
            return


# Station Debug:
def submenu_station_debug(stdscr, state: AppState):
    items = [
        "Connect to specific BSS (BSSID)",
        "Roam to BSS (BSSID)",
        "Get networks (debug)",
        "Set AutoConnect on/off",
        "Back",
    ]
    while True:
        choice = generic_menu(stdscr, "IWCTL STATION DEBUG SUBMENU", items)

        if choice in (0, 1, 2, 3):
            wlan = ask_wlan(stdscr, state, "debug")
            if not wlan:
                continue

            if choice == 0:  # debug <wlan> connect <bssid>
                bssid = input_curses(stdscr, "BSSID (e.g. 00:11:22:33:44:55): ")
                if not bssid:
                    show_output_screen(stdscr, "Error", "N/A", "", "No BSSID.")
                    continue
                args = ["debug", wlan, "connect", bssid]
                title = "Debug - Connect BSSID"

            elif choice == 1:  # roam
                bssid = input_curses(stdscr, "BSSID to roam to: ")
                if not bssid:
                    show_output_screen(stdscr, "Error", "N/A", "", "No BSSID.")
                    continue
                args = ["debug", wlan, "roam", bssid]
                title = "Debug - Roam BSSID"

            elif choice == 2:  # get-networks
                args = ["debug", wlan, "get-networks"]
                title = "Debug - Get networks"

            else:  # autoconnect on/off
                val = input_curses(
                    stdscr,
                    "AutoConnect (on/off): ",
                ).strip().lower()
                if val not in ("on", "off"):
                    show_output_screen(
                        stdscr, "Error", "N/A", "",
                        "Invalid value. Please type 'on' or 'off'.",
                    )
                    continue
                args = ["debug", wlan, "autoconnect", val]
                title = "Debug - AutoConnect"

            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, title, "iwctl " + " ".join(args),
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )

        elif choice == 4:
            return


# ---------- Main TUI ----------

def main_menu(stdscr, state: AppState):
    items = [
        "Adapters",
        "Ad-Hoc",
        "Access Point",
        "Devices",
        "Known Networks",
        "WiFi Simple Configuration",
        "Station",
        "Device Provisioning (DPP)",
        "Shared Code Device Provisioning (PKEX)",
        "Station Debug",
        "version",
        "quit",
    ]

    while True:
        choice = generic_menu(stdscr, "IWCTL HELPER", items)

        if choice == 0:
            submenu_adapters(stdscr, state)
        elif choice == 1:
            submenu_ad_hoc(stdscr, state)
        elif choice == 2:
            submenu_access_point(stdscr, state)
        elif choice == 3:
            submenu_devices(stdscr, state)
        elif choice == 4:
            submenu_known_networks(stdscr, state)
        elif choice == 5:
            submenu_wsc(stdscr, state)
        elif choice == 6:
            submenu_station(stdscr, state)
        elif choice == 7:
            submenu_dpp(stdscr, state)
        elif choice == 8:
            submenu_pkex(stdscr, state)
        elif choice == 9:
            submenu_station_debug(stdscr, state)
        elif choice == 10:  # version
            args = ["version"]
            out, err, rc = run_iwctl(args)
            show_output_screen(
                stdscr, "iwctl version", "iwctl version",
                out, err or ("" if rc == 0 else f"Exit code: {rc}"),
            )
        elif choice == 11:  # quit
            break


def curses_entry(stdscr, state: AppState):
    # Configure curses once, respecting user terminal theme
    curses.curs_set(0)
    stdscr.keypad(True)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()  # key line: use terminal's default fg/bg

    main_menu(stdscr, state)


def main():
    state = AppState.load()
    state = first_run_setup(state)
    curses.wrapper(curses_entry, state)


if __name__ == "__main__":
    main()
