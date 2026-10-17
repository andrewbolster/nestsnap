#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["click", "rich"]
# ///
"""Build a time-compressed timelapse from nestsnap snapshots.

Every frame gets the same screen time, so idle periods (a frame a minute) fly by and motion
(a frame every 5 s) plays ~12x slower. mpdecimate then drops frames that barely differ from
the last kept one (empty room, night) but never more than --max-drop in a row, so day/night
still shows. See https://andrewbolster.info/2014/01/the-making-of-a-timelapse/ - the
decimate settings are still black magic, so tune and rebuild as needed.

Works on any folder laid out as ROOT/<camera>/<YYYY-MM-DD>/snapshots/HHMMSS[_motion].jpg.
Uses ffmpeg from PATH if present, otherwise the linuxserver/ffmpeg Docker image.

\b
  timelapse.py                          yesterday -> <camera>/<day>/timelapse.mp4
  timelapse.py --start D --end D        -> <camera>/timelapses/<start>_<end>.mp4
  timelapse.py --last-week              previous Mon-Sun -> <camera>/weekly/<YYYY>-W<ww>.mp4
"""

import datetime as dt
import os
import pathlib
import shutil
import subprocess
import tempfile

import click
from rich.console import Console

IMAGE = "lscr.io/linuxserver/ffmpeg:latest"
DOCKER_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
LOCAL_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/Library/Fonts/Arial.ttf",
)
console = Console()
Date = click.DateTime(formats=["%Y-%m-%d"])


@click.command(context_settings={"show_default": True}, help=__doc__)
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=pathlib.Path),
    default="/data/snapshots",
    envvar="SNAPSHOT_ROOT",
    help="Folder holding the camera folders.",
)
@click.option("--camera", default="living_room")
@click.option("--start", type=Date, help="First day (default: yesterday).")
@click.option("--end", type=Date, help="Last day (default: --start).")
@click.option("--last-week", is_flag=True, help="The previous Monday-Sunday.")
@click.option("--out", help="Output path, relative to the camera folder.")
@click.option("--timestamp", is_flag=True, help='Burn each frame\'s date/time (and "motion") into the corner.')
@click.option("--fps", default=25)
@click.option("--max-drop", default=15, help="mpdecimate: most consecutive frames dropped (0 = no decimation).")
@click.option("--font", type=click.Path(exists=True, dir_okay=False), help="Font for --timestamp with a local ffmpeg.")
@click.option("--docker", "force_docker", is_flag=True, help="Use the Docker image even if ffmpeg is installed.")
def main(root, camera, start, end, last_week, out, timestamp, fps, max_drop, font, force_docker):
    yesterday = dt.date.today() - dt.timedelta(days=1)
    start, end = start and start.date(), end and end.date()
    if last_week:
        start = yesterday - dt.timedelta(days=yesterday.weekday())
        end = start + dt.timedelta(days=6)
        iso = start.isocalendar()
        out = out or f"weekly/{iso.year}-W{iso.week:02d}.mp4"
    start = start or end or yesterday
    end = end or start
    if end < start:
        raise click.BadParameter("--end is before --start")
    root = root.resolve()
    cam = root / camera
    target = cam / (out or (f"{start}/timelapse.mp4" if start == end else f"timelapses/{start}_{end}.mp4"))

    if shutil.which("ffmpeg") and not force_docker:
        ffmpeg, ffprobe, inside = ["ffmpeg"], ["ffprobe"], str
        font = font or next((f for f in LOCAL_FONTS if os.path.exists(f)), None)
        if timestamp and not font:
            raise click.UsageError("--timestamp needs --font with a local ffmpeg")
    else:
        docker = ["docker", "run", "--rm", "-v", f"{root}:/s"]
        ffmpeg, ffprobe = [*docker, IMAGE], [*docker, "--entrypoint", "ffprobe", IMAGE]
        inside = lambda p: "/s/" + str(pathlib.Path(p).relative_to(root))  # noqa: E731
        font = DOCKER_FONT

    # Stage links named "YYYY-MM-DD HH:MM:SS[  motion]": the glob then sorts across days, and
    # ffmpeg exports each input's file name as frame metadata for drawtext to print.
    stage = pathlib.Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        frames = 0
        day = start
        while day <= end:
            for f in sorted((cam / day.isoformat() / "snapshots").glob("*.jpg")):
                t = f.stem[:6]
                name = f"{day} {t[:2]}:{t[2:4]}:{t[4:6]}" + ("  motion" if f.stem.endswith("_motion") else "")
                try:
                    os.link(f, stage / name)
                except OSError:
                    shutil.copy2(f, stage / name)  # e.g. a share that refuses hard links
                frames += 1
            day += dt.timedelta(days=1)
        if not frames:
            console.print(f"[yellow]{camera} {start}..{end}: no frames[/]")
            return

        filters = [f"mpdecimate=max={max_drop}"] if max_drop else []
        if timestamp:
            filters.append(
                f"drawtext=fontfile={font}:text='%{{metadata\\:lavf.image2dec.source_basename}}'"
                ":x=24:y=h-th-24:fontsize=32:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=10"
            )
        filters += [f"setpts=N/{fps}/TB", "format=yuv420p"]
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp.mp4")
        with console.status(f"encoding {frames} frames from {camera} {start}..{end}"):
            subprocess.run(
                [
                    *ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "image2",
                    "-c:v",
                    "mjpeg",
                    "-export_path_metadata",
                    "1",
                    "-framerate",
                    str(fps),
                    "-pattern_type",
                    "glob",
                    "-i",
                    inside(stage) + "/*",
                    "-vf",
                    ",".join(filters),
                    "-fps_mode",
                    "cfr",
                    "-r",
                    str(fps),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-movflags",
                    "+faststart",
                    inside(tmp),
                ],
                check=True,
            )
        tmp.replace(target)
        seconds = subprocess.run(
            [*ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", inside(target)],
            capture_output=True,
            text=True,
        ).stdout.strip()
        console.print(
            f"[green]{camera} {start}..{end}[/]: {frames} frames -> {target} "
            f"({float(seconds or 0):.1f}s, {target.stat().st_size // 1024} KiB)"
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    main()
