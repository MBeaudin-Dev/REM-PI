"""Session storage and the manual pairing-code fallback.
"""

import json
import os
import urllib.error
import urllib.request

#Fixed system path because the service runs as root.
SESSION_PATH = "/var/lib/rem-field-node/session.json"


def load_session():
    """Returns (server_url, cookie, role, name), or all None if unpaired."""
    if not os.path.exists(SESSION_PATH):
        return None, None, None, None
    with open(SESSION_PATH) as f:
        data = json.load(f)
    return data.get("server_url"), data.get("cookie"), data.get("role"), data.get("name")


def save_session(server_url, cookie, role=None, name=None):
    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    with open(SESSION_PATH, "w") as f:
        json.dump({"server_url": server_url, "cookie": cookie, "role": role, "name": name}, f)


def clear_session():
    if os.path.exists(SESSION_PATH):
        os.remove(SESSION_PATH)


def pair(server_url, pairing_code):
    #POSTs the pairing code and returns (cookie, role, name)

    url = server_url.rstrip("/") + "/api/devices/pair"
    payload = json.dumps({"pairing_code": pairing_code}).encode()
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            set_cookie = response.headers.get("Set-Cookie")
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError("pairing failed ({}): {}".format(e.code, detail))
    except urllib.error.URLError as e:
        raise RuntimeError("could not reach {}: {}".format(url, e.reason))

    if not set_cookie:
        raise RuntimeError("server did not return a session cookie")
    cookie = set_cookie.split(";", 1)[0]  # drop cookie attributes
    role, name = body.get("role"), body.get("name")

    print("Paired as device {} ({})".format(body.get("id"), role))
    save_session(server_url, cookie, role, name)
    return cookie, role, name


def ensure_paired(server_url, pairing_code=None):
    """Returns (server_url, cookie, role, name). Reuses the cached session
    unless a pairing_code is given. Raises RuntimeError if neither exists."""
    cached_url, cached_cookie, cached_role, cached_name = load_session()
    if cached_cookie and not pairing_code:
        return cached_url or server_url, cached_cookie, cached_role, cached_name
    if not pairing_code:
        raise RuntimeError(
            "no cached session found -- pass --pair CODE once, using the code "
            "shown in the admin's Devices tab"
        )
    cookie, role, name = pair(server_url, pairing_code)
    return server_url, cookie, role, name
