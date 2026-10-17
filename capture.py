#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["click", "rich", "httpx"]
# ///
"""Capture camera snapshots from go2rtc: slow when idle, fast around motion.

Frames go to OUT/<YYYY-MM-DD>/snapshots/HHMMSS.jpg (fast-mode frames end _motion).
Motion comes from a Home Assistant event entity (e.g. a Nest camera's motion/person/sound
events): any event within --hold seconds switches to the --fast interval.
The newest frame is served at http://<host>:<port>/<http-path>/latest.jpg.
"""

import datetime as dt
import http.server
import logging
import pathlib
import shutil
import threading
import time

import click
import httpx
from rich.logging import RichHandler

log = logging.getLogger("nestsnap")


def keep_stream_open(go2rtc: str, stream: str) -> None:
    """Hold one consumer on the stream so go2rtc never tears it down (Nest cold starts take ~40 s)."""
    while True:
        try:
            with httpx.stream("GET", f"{go2rtc}/api/stream.mp4", params={"src": stream}, timeout=60) as r:
                r.raise_for_status()
                log.info("stream open")
                for _ in r.iter_raw(65536):
                    pass
            log.warning("stream ended")
        except httpx.HTTPError as e:
            log.warning("stream error: %s", e)
        time.sleep(5)


def serve_latest(port: int, path: str, latest: dict) -> None:
    class Latest(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != f"/{path}/latest.jpg" or not latest.get("jpg"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Last-Modified", latest["at"])
            self.end_headers()
            self.wfile.write(latest["jpg"])

        def log_message(self, *args):
            pass

    http.server.ThreadingHTTPServer(("", port), Latest).serve_forever()


def prune(out: pathlib.Path, keep_days: int) -> None:
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    for day in out.iterdir():
        if day.is_dir() and day.name < cutoff:
            shutil.rmtree(day)
            log.info("pruned %s", day.name)


@click.command(context_settings={"show_default": True})
@click.option(
    "--out",
    type=click.Path(file_okay=False, path_type=pathlib.Path),
    default="/snapshots",
    envvar="OUT",
    help="Camera folder: frames go to OUT/<day>/snapshots/.",
)
@click.option("--go2rtc", default="http://127.0.0.1:1984", envvar="GO2RTC", help="go2rtc API URL.")
@click.option("--stream", default="living_room", envvar="STREAM", help="go2rtc stream name.")
@click.option("--motion-entity", default="event.living_room_living_room_camera_motion", envvar="MOTION_ENTITY")
@click.option("--ha-url", envvar="HA_URL", required=True)
@click.option("--ha-token", envvar="HA_TOKEN", required=True, show_default=False)
@click.option("--idle", default=60, envvar="IDLE", help="Seconds between frames when idle.")
@click.option("--fast", default=5, envvar="FAST", help="Seconds between frames around motion.")
@click.option("--hold", default=30, envvar="HOLD", help="Stay fast this long after the last motion event.")
@click.option("--keep-days", default=0, envvar="KEEP_DAYS", help="Delete local days older than this (0 = never).")
@click.option("--port", default=8089, envvar="PORT", help="Port for latest.jpg (0 = off).")
@click.option(
    "--http-path-file",
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    default="/secrets/http_path",
    envvar="HTTP_PATH_FILE",
    help="File holding the private URL path.",
)
def main(out, go2rtc, stream, motion_entity, ha_url, ha_token, idle, fast, hold, keep_days, port, http_path_file):
    """Capture snapshots from a go2rtc stream, faster while Home Assistant reports motion."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request at INFO
    latest: dict = {}
    threading.Thread(target=keep_stream_open, args=(go2rtc, stream), daemon=True).start()
    if port:
        threading.Thread(
            target=serve_latest, args=(port, http_path_file.read_text().strip(), latest), daemon=True
        ).start()
    log.info(
        "capturing %s: every %ss, %ss within %ss of %s; keeping %s",
        stream,
        idle,
        fast,
        hold,
        motion_entity,
        f"{keep_days} days" if keep_days else "everything",
    )

    ha = httpx.Client(base_url=ha_url, headers={"Authorization": f"Bearer {ha_token}"}, timeout=5)
    cam = httpx.Client(base_url=go2rtc, timeout=60)
    next_prune, active_before, due = 0.0, None, 0.0
    while True:
        # Poll motion every 2 s so a new event switches to fast mode straight away.
        now = dt.datetime.now(dt.UTC)
        try:
            last = dt.datetime.fromisoformat(ha.get(f"/api/states/{motion_entity}").raise_for_status().json()["state"])
            active = (now - last).total_seconds() < hold
        except (httpx.HTTPError, ValueError, KeyError):
            active = False
        if active != active_before:
            log.info("motion: fast mode" if active else "idle mode")
            if active:
                due = 0.0
            active_before = active
        if time.time() >= due:
            due = time.time() + (fast if active else idle)
            try:
                jpg = cam.get("/api/frame.jpeg", params={"src": stream}).raise_for_status().content
                local = dt.datetime.now()
                folder = out / local.strftime("%F") / "snapshots"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{local:%H%M%S}{'_motion' if active else ''}.jpg").write_bytes(jpg)
                latest.update(jpg=jpg, at=now.strftime("%a, %d %b %Y %H:%M:%S GMT"))
            except httpx.HTTPError as e:
                log.warning("grab failed: %s", e)
        if keep_days and time.time() > next_prune:
            prune(out, keep_days)
            next_prune = time.time() + 3600
        time.sleep(2)


if __name__ == "__main__":
    main()
