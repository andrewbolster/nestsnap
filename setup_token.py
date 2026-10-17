#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["click", "rich", "httpx"]
# ///
"""Get a Google Nest (Device Access) refresh token for go2rtc.

The env file (root-only) holds NEST_CLIENT_ID, NEST_CLIENT_SECRET, NEST_PROJECT_ID and
NEST_DEVICE_ID; `exchange` adds NEST_REFRESH_TOKEN. Use the same OAuth client as Home
Assistant's Nest integration (add a second client secret rather than reusing HA's).

\b
  setup_token.py url                 print the consent link
  setup_token.py exchange '<addr>'   swap the redirected address (or bare code) for a token
"""
import os
import pathlib
import re
import urllib.parse

import click
import httpx
from rich.console import Console

# Already registered on the OAuth client for Home Assistant; the page it lands on errors, harmlessly.
REDIRECT = "https://my.home-assistant.io/redirect/oauth"
console = Console()


def read_env(path: pathlib.Path) -> dict[str, str]:
    return dict(line.strip().split("=", 1) for line in path.read_text().splitlines()
                if "=" in line and not line.lstrip().startswith("#"))


@click.group(help=__doc__)
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
              default="/etc/nestsnap/nest.env", show_default=True)
@click.pass_context
def cli(ctx, env_file):
    ctx.obj = env_file


@cli.command()
@click.pass_obj
def url(env_file):
    """Print the Google consent link (keep every device ticked: unticking removes it from HA too)."""
    env = read_env(env_file)
    console.print(f"https://nestservices.google.com/partnerconnections/{env['NEST_PROJECT_ID']}/auth?"
                  + urllib.parse.urlencode({"redirect_uri": REDIRECT, "access_type": "offline", "prompt": "consent",
                                            "client_id": env["NEST_CLIENT_ID"], "response_type": "code",
                                            "scope": "https://www.googleapis.com/auth/sdm.service"}),
                  soft_wrap=True)


@cli.command()
@click.argument("address", nargs=-1, required=True)
@click.pass_obj
def exchange(env_file, address):
    """Swap the redirected ADDRESS (or a bare code) for a refresh token and save it."""
    text = urllib.parse.unquote(" ".join(address))
    match = re.search(r"code=([^&\s]+)", text) or re.search(r"(4/[\w\-]+)", text)
    if not match:
        raise click.BadParameter("no code found", param_hint="ADDRESS")
    env = read_env(env_file)
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": match.group(1), "client_id": env["NEST_CLIENT_ID"], "client_secret": env["NEST_CLIENT_SECRET"],
        "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    if r.is_error:
        raise click.ClickException(f"Google refused the code: {r.json().get('error_description', r.status_code)}")
    token = r.json()
    if "refresh_token" not in token:
        raise click.ClickException("no refresh token returned; get a fresh link and try again")
    devices = httpx.get(f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{env['NEST_PROJECT_ID']}/devices",
                        headers={"Authorization": f"Bearer {token['access_token']}"}).raise_for_status().json()
    for d in devices.get("devices", []):
        rooms = ", ".join(p.get("displayName", "") for p in d.get("parentRelations", []))
        console.print(f"can see: [bold]{d['type'].split('.')[-1]}[/] {rooms}  [dim]{d['name'].rsplit('/', 1)[-1][:12]}…[/]")
    env["NEST_REFRESH_TOKEN"] = token["refresh_token"]
    old = os.umask(0o077)
    try:
        env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    finally:
        os.umask(old)
    console.print(f"[green]saved refresh token to {env_file}[/]")


if __name__ == "__main__":
    cli()
