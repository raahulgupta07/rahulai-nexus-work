#!/usr/bin/env python3
"""CityAgent Insights — Local Runtime Helper (menu-bar app, plug & play).

Non-technical flow:
  1. Download the app from Settings → Local Runtime → double-click.
  2. First launch shows ONE dialog: paste the 6-digit code from the page.
  3. Done. A menu-bar icon shows status; jobs run on this machine.

No terminal, no Python install (PyInstaller bundles everything).
Wraps the same execution core as helper.py (imported), so behavior is
identical to the CLI helper.
"""
import json
import subprocess
import threading
import time
from pathlib import Path

import rumps

import helper  # execution core: ServerSession, execute_job, config helpers

DEFAULT_SERVER = "http://localhost:8095"
BOOTSTRAP = Path(__file__).resolve().parent / "pair_bootstrap.json"


class HelperApp(rumps.App):
    def __init__(self):
        super().__init__("CA", title="○ CityAgent")
        self.status_item = rumps.MenuItem("Status: starting…")
        # Shared folders: persisted in the helper config so they survive
        # restarts and are also picked up by the CLI helper (config_folders).
        self.folders = helper.config_folders()
        self.folders_item = rumps.MenuItem("Shared folders: none")  # no callback = info line
        self.add_folder_item = rumps.MenuItem("Add folder…", callback=self.add_folder)
        self.pause_item = rumps.MenuItem("Pause", callback=self.toggle_pause)
        self.pair_item = rumps.MenuItem("Pair…", callback=self.pair_dialog)
        self.menu = [self.status_item, self.folders_item, None,
                     self.add_folder_item, self.pause_item, self.pair_item]
        self._refresh_folders_item()
        self.paused = False
        self.state = "starting"
        self.jobs_done = 0
        self._session = None
        threading.Thread(target=self.loop, daemon=True).start()

    # ---------------- pairing ----------------
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
            except Exception:
                pass
        # ask the human (one native dialog)
        return self.pair_dialog(None)

    def pair_dialog(self, _):
        w = rumps.Window(
            title="Pair with CityAgent Insights",
            message="Enter the 6-digit code shown in Settings → Local Runtime.\n"
                    "(Server can stay as-is unless told otherwise.)",
            default_text="",
            ok="Pair", cancel="Cancel", dimensions=(220, 24),
        )
        w.icon = None
        resp = w.run()
        if not resp.clicked:
            return False
        code = resp.text.strip()
        server = helper.load_config().get("server") or DEFAULT_SERVER
        if self.claim(server, code):
            rumps.notification("CityAgent Helper", "Paired ✅", "This computer now runs analyses locally.")
            return True
        rumps.notification("CityAgent Helper", "Pairing failed", "Check the code and try again.")
        return False

    def claim(self, server: str, code: str) -> bool:
        import requests
        try:
            r = requests.post(
                f"{server.rstrip('/')}/api/local-runtime/pair/claim",
                json={"code": code, "name": self.device_name(), "helper_version": helper.HELPER_VERSION},
                timeout=15,
            )
            if r.status_code != 200:
                return False
            data = r.json()
            helper.save_config({"server": server.rstrip("/"), "token": data["token"],
                                "runtime_id": data["runtime_id"], "name": self.device_name()})
            return True
        except Exception:
            return False

    @staticmethod
    def device_name() -> str:
        import platform
        return platform.node() or "My Mac"

    # ---------------- shared folders ----------------
    def _refresh_folders_item(self):
        names = [Path(f).name for f in self.folders]
        self.folders_item.title = (
            "Shared folders: " + ", ".join(names) if names else "Shared folders: none"
        )

    def add_folder(self, _):
        """Native macOS folder chooser → whitelist + scan + publish.

        rumps has no picker of its own; AppleScript's `choose folder` is the
        real system dialog. Only the SCHEMA of the chosen folder is published
        (scan_folder's contract) — the data never leaves this machine."""
        try:
            out = subprocess.run(
                ["osascript", "-e",
                 'POSIX path of (choose folder with prompt "Share a folder with CityAgent — '
                 'only table/column names are sent, never the data")'],
                capture_output=True, text=True, timeout=300,
            )
        except Exception:
            rumps.notification("CityAgent Helper", "Add folder failed", "Could not open the folder dialog.")
            return
        if out.returncode != 0:  # user hit Cancel
            return
        path = out.stdout.strip().rstrip("/")
        if not path or not Path(path).is_dir():
            rumps.notification("CityAgent Helper", "Add folder failed", "That folder can't be read.")
            return
        if path in self.folders:
            rumps.notification("CityAgent Helper", "Already shared", Path(path).name)
            return
        self.folders = helper.remember_folder(path)
        self._refresh_folders_item()
        # Publish in the background so the menu stays responsive; the row shows
        # up in the chat paperclip within seconds of the scan landing.
        threading.Thread(target=self._publish_folders, args=(path,), daemon=True).start()

    def _publish_folders(self, new_path: str):
        info = helper.scan_folder(new_path)
        if self._session is not None:
            ok = helper.post_folder_scan(self._session, self.folders, quiet=True)
        else:
            ok = False  # not paired/connected yet — the run loop publishes on connect
        if info.get("error"):
            rumps.notification("CityAgent Helper", "Folder shared with a problem", info["error"])
        else:
            n = len(info.get("tables") or [])
            where = "available in chat" if ok else "will publish once connected"
            rumps.notification(
                "CityAgent Helper", f"Folder shared: {Path(new_path).name}",
                f"{n} table(s) found — schema only, no data uploaded. Now {where}.",
            )

    # ---------------- job loop ----------------
    def set_state(self, s: str):
        self.state = s
        icon = {"online": "●", "running": "⚡", "paused": "‖", "offline": "○"}.get(s, "○")
        self.title = f"{icon} CityAgent"
        self.status_item.title = f"Status: {s} · {self.jobs_done} jobs done"

    def toggle_pause(self, _):
        self.paused = not self.paused
        self.pause_item.title = "Resume" if self.paused else "Pause"
        self.set_state("paused" if self.paused else "online")

    def loop(self):
        while not self.ensure_paired():
            time.sleep(3)
        cfg = helper.load_config()
        self._session = helper.ServerSession(cfg["server"], cfg["token"])
        last_beat = 0.0
        # Publish shared-folder schemas on connect (folders added while offline
        # or before pairing), then periodically so new files show up.
        #
        # allow_empty: publish even an EMPTY list. A folder un-shared since the
        # last session otherwise stays in the server's catalog, gets offered in
        # the chat paperclip, and is then refused by this helper.
        helper.post_folder_scan(self._session, self.folders, quiet=True, allow_empty=True)
        last_scan = time.time()
        cfg_mtime = helper.config_mtime()
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
                # The CLI helper (or a hand-edit) changed the shared-folder
                # config. Adopt it live and republish, so the two front-ends
                # never disagree about what is shared — the old failure was the
                # app offering a folder this process would refuse to query.
                m = helper.config_mtime()
                if m != cfg_mtime:
                    cfg_mtime = m
                    before = list(self.folders)
                    helper.sync_folders(self.folders, [])
                    if self.folders != before:
                        self._refresh_folders_item()
                        helper.post_folder_scan(self._session, self.folders,
                                                quiet=True, allow_empty=True)
                        last_scan = now
                if self.folders and now - last_scan > helper.RESCAN_EVERY_S:
                    helper.post_folder_scan(self._session, self.folders, quiet=True)
                    last_scan = now
                r = self._session.get(f"/api/local-runtime/jobs/next?wait_s={helper.POLL_WAIT_S}",
                                      timeout=helper.POLL_WAIT_S + 10)
                if r.status_code == 401:
                    self.set_state("offline")
                    helper.save_config({})  # unpaired/revoked → forget token
                    time.sleep(5)
                    while not self.ensure_paired():
                        time.sleep(5)
                    cfg = helper.load_config()
                    self._session = helper.ServerSession(cfg["server"], cfg["token"])
                    helper.post_folder_scan(self._session, self.folders,
                                            quiet=True, allow_empty=True)
                    cfg_mtime = helper.config_mtime()
                    self.set_state("online")
                    continue
                if r.status_code != 200:
                    time.sleep(3)
                    continue
                job = r.json()
                if not job.get("job_id"):
                    self.set_state("online")
                    continue
                self.set_state("running")
                result = helper.execute_job(job, self._session, self.folders)
                self._session.post(f"/api/local-runtime/jobs/{job['job_id']}/result", json=result, timeout=60)
                self.jobs_done += 1
                self.set_state("online")
            except Exception:
                self.set_state("offline")
                time.sleep(3)


if __name__ == "__main__":
    HelperApp().run()
