#!/usr/bin/env python3
"""Entry point for the field-node/kiosk Pi service.

The role is assigned by an admin at pairing time:
  - "kiosk"      -- Chromium at {server}/kiosk.html.
  - "field_node" -- Chromium at {server}/field-display.html plus relay control
                    (ws_client.FieldNodeClient).

the Pi advertises itself over mDNS and waits for the server to
push credentials (claim_listener.py). Fallback: --server URL --pair CODE.
"""

import argparse
import asyncio
import sys
import threading
import time
import urllib.error
import urllib.request

import claim_listener
import hardware
import kiosk
import pairing
from ws_client import FieldNodeClient, ServerUnreachable

#The kiosk role has no WebSocket to notice a dead server, so it polls.
KIOSK_PROBE_INTERVAL_SECONDS = 5
KIOSK_GIVE_UP_AFTER_SECONDS = 60


def _device_session_fragment(cookie):
    """Turns "rem_session=device:<id>:<token>" into a URL fragment that
    static/auth.js exchanges for a browser cookie. A fragment is never sent
    to the server, so the token stays out of access logs. Returns "" if the
    cookie isn't in that shape."""
    try:
        # "rem_session=device:<id>:<token>" -> ["device", "<id>", "<token>"]
        _, device_id, token = cookie.split("=", 1)[1].split(":", 2)
    except (IndexError, ValueError):
        return ""
    return "#device-session={}:{}".format(device_id, token)


def _kiosk_session_status(server_url, cookie):
    """Returns "ok", "revoked" (401/403) or "unreachable"."""
    request = urllib.request.Request(
        server_url.rstrip("/") + "/api/devices/me", headers={"Cookie": cookie}
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            return "ok"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "revoked"
        # Any other HTTP error still means the server answered.
        return "ok"
    except Exception:
        return "unreachable"


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Robot Event Manager field-node/kiosk service")
    parser.add_argument(
        "--server",
        help="Server base URL, e.g. http://192.168.1.50:8000 -- only needed with --pair",
    )
    parser.add_argument(
        "--pair",
        metavar="CODE",
        help="Pairing code fallback for networks where mDNS discovery doesn't work",
    )
    parser.add_argument(
        "--pair-only", action="store_true", help="Pair (or wait to be claimed) and exit, without launching anything"
    )
    parser.add_argument(
        "--no-kiosk", action="store_true", help="Don't launch Chromium -- for headless dev/testing"
    )
    return parser.parse_args(argv)


def _wait_to_be_discovered(launch_kiosk):
    hostname = kiosk.local_hostname()
    ip_address = kiosk.local_ip()
    print(
        "Not yet paired. Broadcasting as '{}' ({}) -- waiting for an admin to pair it "
        "from the Devices tab's 'Discovered Field Devices' list.".format(hostname, ip_address)
    )

    # Show the hostname/IP on screen so an operator can match this Pi to the
    # admin's Discovered Field Devices list.
    waiting_chromium = None
    if launch_kiosk:
        waiting_chromium = kiosk.Chromium()
        waiting_chromium.show("file://" + kiosk.write_waiting_page(hostname, ip_address))

    try:
        # Blocks until the server pushes this Pi's credentials.
        server_url, device_id, session_token, role, name = claim_listener.wait_for_claim()
    finally:
        if waiting_chromium is not None:
            waiting_chromium.stop()

    # Same cookie format the server issues on the pairing-code path.
    cookie = "rem_session=device:{}:{}".format(device_id, session_token)
    pairing.save_session(server_url, cookie, role, name)
    print("Claimed as device {} ({}, role={})".format(device_id, name, role))
    return server_url, cookie, role, name


def _run_field_node(server_url, cookie, name, launch_kiosk):
    # Relays are set to disabled as soon as this is created.
    hw = hardware.RelayHardware()
    chromium = kiosk.Chromium() if launch_kiosk else None
    if chromium is not None:
        chromium.show(
            server_url.rstrip("/") + "/field-display.html" + _device_session_fragment(cookie)
        )
        # Waits for X to come up, so keep it off the main thread.
        threading.Thread(
            target=kiosk.detect_and_report_ui_scale, args=(server_url, cookie), daemon=True
        ).start()
    client = FieldNodeClient(server_url, cookie, name, hw)
    try:
        # Runs until the server has been unreachable for GIVE_UP_AFTER_SECONDS.
        asyncio.run(client.run())
    except ServerUnreachable as e:
        #server_url is probably stale, clearing the session makes the next
        #run fall back to auto-discovery.
        print("Giving up on", server_url, "--", e)
        pairing.clear_session()
    except KeyboardInterrupt:
        pass
    finally:
        # Always leave the robots disabled on the way out.
        hw.cleanup()
        if chromium is not None:
            chromium.stop()


def _run_kiosk(server_url, cookie, launch_kiosk):
    if not launch_kiosk:
        print("Paired as a Display Pi. Re-run without --no-kiosk to launch its Chromium display.")
        return
    chromium = kiosk.Chromium()
    chromium.show(server_url.rstrip("/") + "/kiosk.html" + _device_session_fragment(cookie))
    threading.Thread(
        target=kiosk.detect_and_report_ui_scale, args=(server_url, cookie), daemon=True
    ).start()
    unreachable_since = None
    try:
        # Check in with the server until Chromium exits or we give up.
        while chromium.poll() is None:
            status = _kiosk_session_status(server_url, cookie)
            if status == "revoked":
                print("This device's session was revoked or removed -- re-discovering")
                pairing.clear_session()
                break
            if status == "ok":
                unreachable_since = None
            else:
                # Start the give-up timer on the first failed check.
                unreachable_since = unreachable_since or time.monotonic()
                if time.monotonic() - unreachable_since > KIOSK_GIVE_UP_AFTER_SECONDS:
                    print(
                        "Giving up on", server_url,
                        "-- unreachable for over", KIOSK_GIVE_UP_AFTER_SECONDS, "s",
                    )
                    pairing.clear_session()
                    break
            time.sleep(KIOSK_PROBE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        chromium.stop()


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    launch_kiosk = not args.no_kiosk

    cached_url, cached_cookie, cached_role, cached_name = pairing.load_session()

    # Credentials come from, in order: an explicit --pair code, the saved
    # session, or waiting to be claimed over the network.
    try:
        if args.pair:
            server_url = args.server or cached_url
            if not server_url:
                print("No server URL given -- pass --server http://<host>:<port>", file=sys.stderr)
                return 1
            server_url, cookie, role, name = pairing.ensure_paired(server_url, args.pair)
        elif cached_cookie:
            server_url, cookie, role, name = cached_url, cached_cookie, cached_role, cached_name
        else:
            server_url, cookie, role, name = _wait_to_be_discovered(launch_kiosk)
    except RuntimeError as e:
        print("Pairing error:", e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 1

    if args.pair_only:
        print("Paired successfully as '{}' (role={})".format(name, role))
        return 0

    if role == "field_node":
        _run_field_node(server_url, cookie, name, launch_kiosk)
    elif role == "kiosk":
        _run_kiosk(server_url, cookie, launch_kiosk)
    else:
        print("Paired with role '{}', which this firmware doesn't know how to run.".format(role), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
