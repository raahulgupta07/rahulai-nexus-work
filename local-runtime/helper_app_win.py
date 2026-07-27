#!/usr/bin/env python3
"""CityAgent Insights — Local Runtime Helper (Windows system-tray app).

Windows sibling of the macOS menu-bar app (``helper_app.py``). Same flow:

  1. Download the app from Settings -> Local Runtime, run it.
  2. First launch shows ONE dialog: type the 6-digit code from the page.
  3. Done. A tray icon shows status; jobs run on this machine.

No terminal, no Python install (PyInstaller bundles everything).
Wraps the same execution core as ``helper.py`` (imported), so behavior is
identical to the CLI helper and to the Mac app.

Build: see BUILD-WINDOWS.md.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

# GUI deps are Windows/runtime-only. They are absent on the dev Mac, so the
# imports must never hard-fail at import time (module still compiles/loads;
# a clean message is printed if the app is actually started without them).
try:  # pragma: no cover - platform dependent
    import pystray
except Exception:  # noqa: BLE001
    pystray = None

try:  # pragma: no cover - platform dependent
    from PIL import Image, ImageDraw
except Exception:  # noqa: BLE001
    Image = None
    ImageDraw = None

try:  # pragma: no cover - platform dependent
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog
except Exception:  # noqa: BLE001
    tk = None
    filedialog = None
    messagebox = None
    simpledialog = None

import helper  # execution core: ServerSession, execute_job, config helpers

APP_NAME = "CityAgent Helper"
DEFAULT_SERVER = "http://localhost:8095"

# Beside the .exe when frozen by PyInstaller, beside this file when run from
# source. Lets a personalized download ship a pre-filled pairing code.
_BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
BOOTSTRAP = _BASE_DIR / "pair_bootstrap.json"

# tray colours: green = connected, blue = running, amber = paused, grey = offline
STATE_COLORS = {
    "online": (34, 197, 94, 255),
    "running": (59, 130, 246, 255),
    "paused": (245, 158, 11, 255),
    "offline": (148, 163, 184, 255),
}
STATE_LABELS = {
    "online": "connected",
    "running": "running a job",
    "paused": "paused",
    "offline": "offline",
    "starting": "starting…",
}


def make_icon(state: str):
    """Draw the tray icon in code (no asset files to bundle): a coloured disc
    with a white "C" arc. Colour carries the state."""
    if Image is None:
        return None
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = STATE_COLORS.get(state, STATE_COLORS["offline"])
    d.ellipse((2, 2, size - 3, size - 3), fill=color)
    inset = 17
    d.arc((inset, inset, size - inset - 1, size - inset - 1),
          start=40, end=320, fill=(255, 255, 255, 255), width=7)
    return img


# --------------------------------------------------------------------------- #
#  Config helpers (merge-safe — save_config replaces the whole file)
# --------------------------------------------------------------------------- #

def cfg_update(**changes) -> dict:
    cfg = helper.load_config()
    cfg.update(changes)
    helper.save_config(cfg)
    return cfg


def allowed_folders() -> list:
    # Single source of truth shared with the CLI and the Mac app (reads the
    # current "folders" key plus this app's legacy "allowed_folders" key).
    return helper.config_folders()


class HelperTrayApp:
    def __init__(self):
        self.paused = False
        self.state = "starting"
        self.jobs_done = 0
        self.last_error = ""
        self._session = None
        self._tk = None
        self._dialog_lock = threading.Lock()
        self.icon = None

    # ---------------- tray plumbing ----------------
    def build_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self.status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pair…", self.on_pair),
            pystray.MenuItem(lambda _: "Resume" if self.paused else "Pause", self.on_toggle_pause),
            pystray.MenuItem("Allow folder…", self.on_allow_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Unpair", self.on_unpair),
            pystray.MenuItem("Quit", self.on_quit),
        )
        self.icon = pystray.Icon("cityagent_helper", make_icon("offline"), APP_NAME, menu)
        return self.icon

    def status_text(self) -> str:
        label = STATE_LABELS.get(self.state, self.state)
        folders = allowed_folders()
        parts = [f"Status: {label}", f"{self.jobs_done} jobs done"]
        if folders:
            parts.append(f"{len(folders)} folder(s) allowed")
        return " · ".join(parts)

    def set_state(self, state: str):
        self.state = state
        if not self.icon:
            return
        try:
            self.icon.icon = make_icon("offline" if state == "starting" else state)
            self.icon.title = f"{APP_NAME} — {STATE_LABELS.get(state, state)}"
            self.icon.update_menu()
        except Exception:  # noqa: BLE001 — tray updates are best-effort
            pass

    def notify(self, message: str, title: str = APP_NAME):
        try:
            if self.icon:
                self.icon.notify(message, title)
        except Exception:  # noqa: BLE001 — notifications are optional on Windows
            pass

    # ---------------- tkinter dialogs (marshalled onto the Tk main thread) ----
    def _dialog(self, fn, *args, **kwargs):
        """Run a tkinter dialog on the Tk main thread and block the calling
        (worker/tray) thread until the user answers. Tk is not thread-safe, so
        every dialog goes through the main loop."""
        if self._tk is None or fn is None:
            return None
        with self._dialog_lock:
            box = {}
            done = threading.Event()

            def runner():
                try:
                    # The root stays hidden; -topmost pulls the child dialog in
                    # front of whatever the user is working in.
                    self._tk.attributes("-topmost", True)
                    box["value"] = fn(*args, **kwargs)
                except Exception:  # noqa: BLE001
                    box["value"] = None
                finally:
                    try:
                        self._tk.attributes("-topmost", False)
                    except Exception:  # noqa: BLE001
                        pass
                    done.set()

            self._tk.after(0, runner)
            done.wait(timeout=600)
            return box.get("value")

    def ask_code(self):
        return self._dialog(
            simpledialog.askstring,
            "Pair with CityAgent Insights",
            "Enter the 6-digit code shown in Settings → Local Runtime.",
            parent=self._tk,
        )

    def ask_server(self, current: str):
        return self._dialog(
            simpledialog.askstring,
            "CityAgent server",
            "Address of your CityAgent Insights app\n(e.g. https://insights.example.com)",
            initialvalue=current,
            parent=self._tk,
        )

    def ask_folder(self):
        return self._dialog(
            filedialog.askdirectory,
            title="Choose a folder the agent may analyse (files never leave this PC)",
            mustexist=True,
        )

    def say(self, title: str, message: str):
        self._dialog(messagebox.showinfo, title, message, parent=self._tk)

    # ---------------- pairing ----------------
    @staticmethod
    def device_name() -> str:
        import platform
        return platform.node() or "My PC"

    def claim(self, server: str, code: str) -> bool:
        import requests
        try:
            r = requests.post(
                f"{server.rstrip('/')}/api/local-runtime/pair/claim",
                json={"code": code, "name": self.device_name(),
                      "helper_version": helper.HELPER_VERSION},
                timeout=15,
            )
            if r.status_code != 200:
                self.last_error = f"pairing rejected ({r.status_code})"
                return False
            data = r.json()
            cfg_update(server=server.rstrip("/"), token=data["token"],
                       runtime_id=data["runtime_id"], name=self.device_name())
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            return False

    def ensure_paired(self) -> bool:
        cfg = helper.load_config()
        if cfg.get("token"):
            return True
        # bundled bootstrap (personalized download) — pair silently
        if BOOTSTRAP.exists():
            try:
                b = json.loads(BOOTSTRAP.read_text())
                if self.claim(b.get("server", DEFAULT_SERVER), str(b.get("code", ""))):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return self.pair_dialog()

    def pair_dialog(self, *_) -> bool:
        cfg = helper.load_config()
        server = cfg.get("server")
        if not server:
            server = self.ask_server(DEFAULT_SERVER)
            if not server:
                return False
            server = server.strip()
            if not server.startswith(("http://", "https://")):
                server = "https://" + server
        code = self.ask_code()
        if not code:
            return False
        if self.claim(server, code.strip()):
            self.notify("Paired. This computer now runs analyses locally.")
            return True
        self.notify(f"Pairing failed — {self.last_error or 'check the code and try again'}.")
        return False

    # ---------------- menu callbacks ----------------
    def on_pair(self, *_):
        threading.Thread(target=self.pair_dialog, daemon=True).start()

    def on_toggle_pause(self, *_):
        self.paused = not self.paused
        self.set_state("paused" if self.paused else "online")

    def on_allow_folder(self, *_):
        def worker():
            path = self.ask_folder()
            if not path:
                return
            path = os.path.normpath(path)
            if path in allowed_folders():
                self.notify(f"Already shared: {path}")
                return
            helper.remember_folder(path)
            self.set_state(self.state)  # refresh the status line
            # Publish the schema so the folder appears in the chat paperclip
            # menu (schema only — table/column names, never the data).
            info = helper.scan_folder(path)
            ok = False
            if self._session is not None:
                ok = helper.post_folder_scan(self._session, allowed_folders(), quiet=True)
            if info.get("error"):
                self.notify(f"Folder shared with a problem: {info['error']}")
            else:
                n = len(info.get("tables") or [])
                where = "available in chat" if ok else "will publish once connected"
                self.notify(f"Folder shared: {path}\n{n} table(s) found — schema only, "
                            f"never the data. Now {where}.")
        threading.Thread(target=worker, daemon=True).start()

    def on_unpair(self, *_):
        cfg = helper.load_config()
        helper.save_config({"server": cfg.get("server"),
                            "allowed_folders": cfg.get("allowed_folders") or []})
        self._session = None
        self.set_state("offline")
        self.notify("Unpaired. Analyses run in the cloud again.")

    def on_quit(self, *_):
        try:
            if self.icon:
                self.icon.stop()
        finally:
            if self._tk is not None:
                self._tk.after(0, self._tk.quit)

    # ---------------- job loop ----------------
    def loop(self):
        while not self.ensure_paired():
            time.sleep(5)
        cfg = helper.load_config()
        self._session = helper.ServerSession(cfg["server"], cfg["token"])
        last_beat = 0.0
        # Publish shared-folder schemas on connect (folders added while offline
        # or by an earlier build that never published), then periodically.
        #
        # allow_empty: an EMPTY list must be published too. Otherwise a folder
        # un-shared since the last session stays in the server's catalog, is
        # offered in the chat paperclip, and is then refused by this helper.
        helper.post_folder_scan(self._session, allowed_folders(), quiet=True,
                                allow_empty=True)
        last_scan = time.time()
        self.set_state("online")
        while True:
            try:
                if self.paused:
                    time.sleep(1)
                    continue
                now = time.time()
                if now - last_beat > helper.HEARTBEAT_EVERY_S:
                    self._session.post("/api/local-runtime/heartbeat", timeout=10)
                    last_beat = now
                if allowed_folders() and now - last_scan > helper.RESCAN_EVERY_S:
                    helper.post_folder_scan(self._session, allowed_folders(), quiet=True)
                    last_scan = now
                r = self._session.get(
                    f"/api/local-runtime/jobs/next?wait_s={helper.POLL_WAIT_S}",
                    timeout=helper.POLL_WAIT_S + 10,
                )
                if r.status_code == 401:
                    self.set_state("offline")
                    self.on_unpair()  # forget the revoked token, keep server/folders
                    time.sleep(5)
                    while not self.ensure_paired():
                        time.sleep(5)
                    cfg = helper.load_config()
                    self._session = helper.ServerSession(cfg["server"], cfg["token"])
                    helper.post_folder_scan(self._session, allowed_folders(), quiet=True,
                                            allow_empty=True)
                    self.set_state("online")
                    continue
                if r.status_code != 200:
                    time.sleep(3)
                    continue
                job = r.json()
                if not job.get("job_id"):
                    if self.state != "paused":
                        self.set_state("online")
                    continue
                self.set_state("running")
                result = helper.execute_job(job, self._session, allowed_folders())
                self._session.post(f"/api/local-runtime/jobs/{job['job_id']}/result",
                                   json=result, timeout=60)
                self.jobs_done += 1
                self.set_state("online")
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                self.set_state("offline")
                time.sleep(3)

    # ---------------- entry point ----------------
    def run(self):
        self.build_icon()
        threading.Thread(target=self.loop, daemon=True).start()
        if tk is None:
            # No tkinter: tray still works, dialogs don't (pair from the CLI).
            self.icon.run()
            return
        self._tk = tk.Tk()
        self._tk.withdraw()
        # pystray runs its own Win32 message loop on whichever thread calls
        # run(); Tk owns the main thread so dialogs can be created there.
        threading.Thread(target=self.icon.run, daemon=True).start()
        self._tk.mainloop()


def main() -> int:
    if pystray is None or Image is None:
        sys.stderr.write(
            "This app needs pystray and Pillow:\n"
            "    pip install pystray pillow\n"
            "(the CLI helper works without them: python helper.py run)\n"
        )
        return 1
    HelperTrayApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
