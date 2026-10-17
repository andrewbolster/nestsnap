# nestsnap

Snapshots and time-compressed timelapses from a Google Nest camera, via
[go2rtc](https://github.com/AlexxIT/go2rtc) and Home Assistant.

Home Assistant's Nest integration can't take stills from current Nest cameras (they're
WebRTC-only), so go2rtc holds the camera's stream open and nestsnap grabs frames from it:

- **every 60 s** normally;
- **every 5 s** while Home Assistant reports a motion, person or sound event, and for 30 s after.

Each day's frames become a timelapse where every frame gets the same screen time, so quiet
hours fly by and anything that happened plays about 12x slower. `mpdecimate` drops
near-identical frames (an empty room, the night) but never too many in a row, so day/night
still shows; the idea goes back to
[The Making of a Timelapse](https://andrewbolster.info/2014/01/the-making-of-a-timelapse/)
(2014), including the caveat that the decimate settings are black magic.

## Layout

```
<root>/<camera>/<YYYY-MM-DD>/snapshots/HHMMSS.jpg          idle frame
                             snapshots/HHMMSS_motion.jpg   fast-mode frame
                             timelapse.mp4                 that day's timelapse
<root>/<camera>/weekly/<YYYY>-W<ww>.mp4                    Monday's re-run over last week
<root>/<camera>/timelapses/<start>_<end>.mp4               any other range
```

Frames are written to local disk first and copied to a mirror (e.g. an NFS share that
Home Assistant mounts as Media) every 5 minutes. Nothing is deleted anywhere unless you set
`KEEP_DAYS` on the capture service.

## Pieces

| | |
|---|---|
| `capture.py` | Grabs frames from go2rtc; polls Home Assistant for motion. Runs in the `capture` container. |
| `timelapse.py` | Builds a timelapse for a day or a date range. Works on any folder with the layout above (`--root`), with a local ffmpeg or the linuxserver/ffmpeg image. |
| `setup_token.py` | One-off Google OAuth dance for go2rtc's refresh token. |
| `deploy/compose.yml` | go2rtc + capture stack (Portainer or `docker compose`). |
| `deploy/systemd/` | Mirror every 5 min, daily timelapse 00:20, weekly timelapse Monday 01:00. |

All scripts are [uv](https://docs.astral.sh/uv/) scripts with inline dependencies (`click`, `rich`, `httpx`).

```
./timelapse.py                                   # yesterday
./timelapse.py --start 2026-10-12 --end 2026-10-18 --timestamp
./timelapse.py --last-week --timestamp
./timelapse.py --root /mnt/nas/cameras --start 2026-10-17 --max-drop 0
```

## Setup

Needs a Nest camera already working in Home Assistant's Google Nest integration (so you
have a Device Access project and an OAuth client), Docker, and `uv` on root's PATH.

1. `git clone https://github.com/andrewbolster/nestsnap /opt/nestsnap && sudo /opt/nestsnap/deploy/install.sh`
2. `/etc/nestsnap/nest.env` (mode 600):
   ```
   NEST_CLIENT_ID=...apps.googleusercontent.com
   NEST_CLIENT_SECRET=...        # add a second secret on the client; leave Home Assistant's alone
   NEST_PROJECT_ID=...           # Device Access project ID
   NEST_DEVICE_ID=...            # the camera's device ID (last part of the HA device identifier)
   ```
3. `sudo ./setup_token.py url`, open the link, **keep every device ticked** (unticking one
   removes it from Home Assistant too), then `sudo ./setup_token.py exchange '<the address you land on>'`.
   The redirect reuses Home Assistant's `my.home-assistant.io` URI, which is already registered
   on the client; the "invalid parameters" page you land on is expected.
4. `/etc/nestsnap/ha.env` (mode 600): `HA_URL=http://homeassistant:8123` and `HA_TOKEN=<long-lived token>`.
5. Edit `/etc/nestsnap/nestsnap.env` (snapshot root and mirror destination), adjust
   `deploy/compose.yml` (camera folder, motion entity), and deploy it.

The newest frame is served at `http://<host>:8089/<contents of /etc/nestsnap/http_path>/latest.jpg`,
e.g. for a Home Assistant camera card. go2rtc only listens on localhost, because its UI shows
stream sources, which include the token.

## Development

```
uvx pre-commit install     # ruff (lint + format), gitleaks (secrets), shellcheck, basic checks
uvx pre-commit run --all-files
```

The same hooks run in GitHub Actions on every push. Deploy on the host with
`git -C /opt/nestsnap pull`, then restart the stack (capture) or nothing (timelapse/mirror pick
up changes on their next run).
