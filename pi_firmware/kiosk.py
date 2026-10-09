"""A waiting page on chromium shows the hostname/IP before pairing, then the role's page after (see main.py).

Raspberry Pi OS Lite has no X server running, so this starts a bare one via
`xinit` with Chromium as its only client
"""

import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


# Pre-pairing waiting page with an animated gear pair. No external
# fonts/CDNs: the Pi may have no internet yet. Uses @@TOKEN@@ substitution
# because the CSS/JS is full of literal braces.
_WAITING_PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Robot Event Manager - Waiting to be Paired</title>
<style>
  :root {
    --bg: #0b0d10;
    --line: #262b32;
    --accent: #ffb454;
    --accent-dim: #8a6a3a;
    --text: #e8e6e1;
    --text-dim: #8b909a;
    --steel-hi: #454c56;
    --steel-lo: #191c20;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    height: 100vh;
    width: 100vw;
    background: var(--bg);
    color: var(--text);
    font-family: ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    -webkit-font-smoothing: antialiased;
  }

  .frame { position: fixed; inset: 28px; pointer-events: none; }
  .frame .corner { position: absolute; width: 34px; height: 34px; border: 2px solid var(--accent-dim); }
  .frame .corner.tl { top: 0; left: 0; border-right: none; border-bottom: none; }
  .frame .corner.tr { top: 0; right: 0; border-left: none; border-bottom: none; }
  .frame .corner.bl { bottom: 0; left: 0; border-right: none; border-top: none; }
  .frame .corner.br { bottom: 0; right: 0; border-left: none; border-top: none; }
  .frame .tag {
    position: absolute; top: 0; left: 50px;
    font-size: 0.7rem; letter-spacing: 0.18em; color: var(--text-dim); text-transform: uppercase;
  }
  .frame .tag.right { right: 50px; left: auto; text-align: right; }

  .stage { display: flex; flex-direction: column; align-items: center; gap: 2.2rem; }

  .eyebrow {
    font-family: ui-sans-serif, sans-serif;
    font-weight: 600; font-size: 1.15rem; letter-spacing: 0.42em;
    text-transform: uppercase; color: var(--accent); text-align: center;
  }

  /* --- gear rig --- */
  .gear-rig {
    position: relative;
    width: 340px;
    height: 340px;
    --spin: 0;
  }
  .gear-rig svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    filter: drop-shadow(0 8px 16px rgba(0, 0, 0, 0.55));
  }
  #mainSpin, #secSpin { will-change: transform; }

  .hub {
    position: absolute;
    left: 44.1%;
    top: 57.4%;
    width: 15px;
    height: 15px;
    margin: -7.5px 0 0 -7.5px;
    border-radius: 50%;
    background: var(--accent);
    z-index: 3;
    box-shadow:
      0 0 0 4px rgba(255, 180, 84, calc(0.08 + var(--spin) * 0.2)),
      0 0 calc(8px + var(--spin) * 30px) calc(2px + var(--spin) * 8px) rgba(255, 180, 84, calc(0.3 + var(--spin) * 0.55));
  }

  /* --- identity plate --- */
  .plate { text-align: center; }
  .plate .label {
    font-family: ui-sans-serif, sans-serif;
    font-size: 0.75rem; letter-spacing: 0.3em; text-transform: uppercase;
    color: var(--text-dim); margin-bottom: 0.4rem;
  }
  .plate .ip { font-size: 2.6rem; font-weight: 700; letter-spacing: 0.02em; color: var(--text); }
  .plate .ip .cursor {
    display: inline-block; width: 0.5ch; color: var(--accent);
    animation: blink 1.1s step-end infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
  .plate .hostname { margin-top: 0.5rem; font-size: 1.15rem; color: var(--text-dim); letter-spacing: 0.06em; }
  .plate .hostname::before {
    content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent); margin-right: 0.6em; vertical-align: middle;
  }

  /* --- status bar --- */
  .statusbar {
    position: fixed; left: 0; right: 0; bottom: 56px;
    display: flex; flex-direction: column; align-items: center; gap: 0.9rem;
  }
  .status-line {
    font-size: 0.85rem; color: var(--text-dim); min-height: 1.2em;
    letter-spacing: 0.02em; transition: opacity 0.3s ease;
  }
  .hint {
    max-width: 34rem; text-align: center;
    font-family: ui-sans-serif, sans-serif; font-size: 1rem; letter-spacing: 0.02em;
    color: var(--text-dim); line-height: 1.4; padding-top: 0.4rem; border-top: 1px solid var(--line);
  }
  .hint b { color: var(--text); font-weight: 600; }

  @media (prefers-reduced-motion: reduce) {
    .plate .ip .cursor { animation: none; }
  }
</style>
</head>
<body>

<div class="frame">
  <div class="corner tl"></div>
  <div class="corner tr"></div>
  <div class="corner bl"></div>
  <div class="corner br"></div>
  <div class="tag">REM&nbsp;FIELD&nbsp;NODE</div>
  <div class="tag right">STANDBY</div>
</div>

<div class="stage">
  <div class="eyebrow">Waiting to be paired</div>

  <div class="gear-rig" id="gearRig">
    <svg id="gearsSvg" viewBox="0 0 340 340">
      <defs>
        <radialGradient id="steel" cx="35%" cy="30%">
          <stop offset="0%" stop-color="#454c56"></stop>
          <stop offset="60%" stop-color="#2c3138"></stop>
          <stop offset="100%" stop-color="#191c20"></stop>
        </radialGradient>
      </defs>
      <g id="secWrap"><g id="secSpin"></g></g>
      <g id="mainWrap"><g id="mainSpin"></g></g>
    </svg>
    <div class="hub"></div>
  </div>

  <div class="plate">
    <div class="label">This device</div>
    <div class="ip">@@IP_ADDRESS@@<span class="cursor">_</span></div>
    <div class="hostname">@@HOSTNAME@@</div>
  </div>
</div>

<div class="statusbar">
  <div class="status-line" id="statusLine">Broadcasting on the local network&hellip;</div>
  <div class="hint">
    In the admin's <b>Devices</b> tab, find this hostname under<br>
    &ldquo;Discovered Field Devices&rdquo; and pair it.
  </div>
</div>

<script>
  var SVG_NS = "http://www.w3.org/2000/svg";

  // ---- gear geometry: shared module so tooth pitch matches between gears ----
  var MODULE = 10;
  var MAIN_TEETH = 20, SEC_TEETH = 9;
  var ADDENDUM = MODULE * 0.55, DEDENDUM = MODULE * 0.7;

  function gearRadii(teeth) {
    var pitchR = MODULE * teeth / 2;
    return { pitchR: pitchR, outerR: pitchR + ADDENDUM, rootR: pitchR - DEDENDUM };
  }
  var mainR = gearRadii(MAIN_TEETH);
  var secR = gearRadii(SEC_TEETH);

  var MAIN_CX = 150, MAIN_CY = 195;
  var MESH_ANGLE = -48; // degrees, direction from main gear to secondary gear
  var CENTER_DIST = mainR.pitchR + secR.pitchR;
  var meshRad = MESH_ANGLE * Math.PI / 180;
  var SEC_CX = MAIN_CX + CENTER_DIST * Math.cos(meshRad);
  var SEC_CY = MAIN_CY + CENTER_DIST * Math.sin(meshRad);

  function polarXY(r, deg) {
    var rad = deg * Math.PI / 180;
    return { x: r * Math.cos(rad), y: r * Math.sin(rad) };
  }
  function fmt(p) { return p.x.toFixed(2) + "," + p.y.toFixed(2); }

  // A real gear tooth's flanks curve from a wider root to a narrower tip,
  // joined by curved (not straight) flanks, with rounded arcs at both the
  // tip and the valley floor between teeth. Built as one continuous closed
  // path per gear rather than stacked straight-sided shapes.
  function buildToothPath(teeth, outerR, rootR) {
    var step = 360 / teeth;
    var baseHalf = step * 0.28;
    var tipHalf = step * 0.16;
    var ctrlRadius = rootR + (outerR - rootR) * 0.62;
    var d = "";
    var firstRootL = null;

    for (var i = 0; i < teeth; i++) {
      var c = i * step;
      var aRootL = c - baseHalf, aTipL = c - tipHalf, aTipR = c + tipHalf, aRootR = c + baseHalf;
      var rootL = polarXY(rootR, aRootL);
      var tipL = polarXY(outerR, aTipL);
      var tipR = polarXY(outerR, aTipR);
      var rootR2 = polarXY(rootR, aRootR);
      var ctrlL = polarXY(ctrlRadius, aRootL + (aTipL - aRootL) * 0.5);
      var ctrlR = polarXY(ctrlRadius, aTipR + (aRootR - aTipR) * 0.5);

      if (i === 0) {
        firstRootL = rootL;
        d += "M " + fmt(rootL) + " ";
      }
      d += "Q " + fmt(ctrlL) + " " + fmt(tipL) + " ";           // curved flank up to the tip
      d += "A " + outerR + " " + outerR + " 0 0 1 " + fmt(tipR) + " "; // rounded tip
      d += "Q " + fmt(ctrlR) + " " + fmt(rootR2) + " ";          // curved flank back down
      var nextRootL = (i === teeth - 1) ? firstRootL : polarXY(rootR, (i + 1) * step - baseHalf);
      d += "A " + rootR + " " + rootR + " 0 0 1 " + fmt(nextRootL) + " "; // rounded valley floor
    }
    return d + "Z";
  }

  // Builds one gear (teeth + body + bolt holes + bore) centered on local (0,0).
  function buildGear(g, teeth, outerR, rootR) {
    var body = document.createElementNS(SVG_NS, "path");
    body.setAttribute("d", buildToothPath(teeth, outerR, rootR));
    body.setAttribute("fill", "url(#steel)");
    body.setAttribute("stroke", "var(--line)");
    body.setAttribute("stroke-width", "1.5");
    body.setAttribute("stroke-linejoin", "round");
    g.appendChild(body);

    var boltCount = teeth >= 12 ? 6 : 5;
    var boltR = rootR * 0.56;
    var holeR = rootR * 0.1;
    for (var b = 0; b < boltCount; b++) {
      var ang = b * (360 / boltCount) * Math.PI / 180;
      var hole = document.createElementNS(SVG_NS, "circle");
      hole.setAttribute("cx", (boltR * Math.cos(ang)).toFixed(2));
      hole.setAttribute("cy", (boltR * Math.sin(ang)).toFixed(2));
      hole.setAttribute("r", holeR);
      hole.setAttribute("fill", "var(--bg)");
      hole.setAttribute("stroke", "var(--line)");
      hole.setAttribute("stroke-width", "1");
      g.appendChild(hole);
    }

    var bore = document.createElementNS(SVG_NS, "circle");
    bore.setAttribute("cx", 0); bore.setAttribute("cy", 0); bore.setAttribute("r", rootR * 0.32);
    bore.setAttribute("fill", "var(--bg)");
    bore.setAttribute("stroke", "var(--line)");
    bore.setAttribute("stroke-width", "1.5");
    g.appendChild(bore);
  }

  var mainWrap = document.getElementById("mainWrap");
  var secWrap = document.getElementById("secWrap");
  mainWrap.setAttribute("transform", "translate(" + MAIN_CX + "," + MAIN_CY + ")");
  secWrap.setAttribute("transform", "translate(" + SEC_CX.toFixed(2) + "," + SEC_CY.toFixed(2) + ")");

  var mainSpin = document.getElementById("mainSpin");
  var secSpin = document.getElementById("secSpin");
  buildGear(mainSpin, MAIN_TEETH, mainR.outerR, mainR.rootR);
  buildGear(secSpin, SEC_TEETH, secR.outerR, secR.rootR);

  // Static phase offset so a tooth always nests into a gap where the two
  // gears meet along the line joining their centers.
  var phiMainToSec = Math.atan2(SEC_CY - MAIN_CY, SEC_CX - MAIN_CX) * 180 / Math.PI;
  var phiSecToMain = phiMainToSec + 180;
  var R_MAIN = phiMainToSec - (360 / MAIN_TEETH) / 2; // a gap faces the secondary gear
  var R_SEC = phiSecToMain;                            // a tooth faces the main gear

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduceMotion) {
    mainSpin.style.transform = "rotate(" + R_MAIN + "deg)";
    secSpin.style.transform = "rotate(" + R_SEC + "deg)";
    document.getElementById("gearRig").style.setProperty("--spin", "0.4");
  } else {
    (function () {
      var rig = document.getElementById("gearRig");
      var mainAngle = 0, secAngle = 0, mainVel = 0;
      var phase, phaseStart, phaseDur, vFrom, vTo, maxVel;

      function rand(min, max) { return min + Math.random() * (max - min); }
      function easeInQuad(t) { return t * t; }
      function easeOutQuad(t) { return 1 - (1 - t) * (1 - t); }

      function enterPhase(p) {
        phase = p;
        phaseStart = performance.now();
        if (p === "idle") { vFrom = 0; vTo = 0; phaseDur = rand(700, 2800); }
        else if (p === "rev") { vFrom = 0; maxVel = rand(430, 780); vTo = maxVel; phaseDur = rand(650, 1250); }
        else if (p === "cruise") { vFrom = maxVel; vTo = maxVel; phaseDur = rand(900, 3600); }
        else if (p === "decel") { vFrom = maxVel; vTo = 0; phaseDur = rand(800, 1700); }
      }
      enterPhase("idle");

      var lastT = performance.now();
      function frame(now) {
        var dt = (now - lastT) / 1000; lastT = now;
        var t = Math.min(1, (now - phaseStart) / phaseDur);
        var eased = t;
        if (phase === "rev") eased = easeInQuad(t);
        else if (phase === "decel") eased = easeOutQuad(t);

        mainVel = vFrom + (vTo - vFrom) * eased;
        mainAngle += mainVel * dt;
        secAngle -= mainVel * (MAIN_TEETH / SEC_TEETH) * dt;

        mainSpin.style.transform = "rotate(" + (R_MAIN + mainAngle) + "deg)";
        secSpin.style.transform = "rotate(" + (R_SEC + secAngle) + "deg)";
        rig.style.setProperty("--spin", Math.min(1, mainVel / 700).toFixed(3));

        if (t >= 1) {
          if (phase === "idle") enterPhase("rev");
          else if (phase === "rev") enterPhase("cruise");
          else if (phase === "cruise") enterPhase("decel");
          else enterPhase("idle");
        }
        requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    })();
  }

  // ---- rotating status line ----
  var messages = [
    "Broadcasting on the local network…",
    "Advertising via mDNS as “@@HOSTNAME@@.local”…",
    "Listening for the admin’s claim…"
  ];
  var mi = 0;
  var statusEl = document.getElementById("statusLine");
  setInterval(function () {
    mi = (mi + 1) % messages.length;
    statusEl.style.opacity = 0;
    setTimeout(function () {
      statusEl.textContent = messages[mi];
      statusEl.style.opacity = 1;
    }, 300);
  }, 3400);
</script>
</body></html>
"""

CHROMIUM_KIOSK_ARGS = [
    "--kiosk",
    "--noerrdialogs",
    "--disable-infobars",
    "--incognito",
    "--no-sandbox",  # required to run as root
]

_XINIT_SERVER_ARGS = [":0", "-nocursor"]


def _ensure_kms_device_pinned():
    """On the Pi 5 the render-only GPU (v3d) and the display controller are
    separate DRM cards, and Xorg may pick the render-only one and fail with
    "Cannot run in framebuffer mode". If exactly one non-v3d card exists, pin
    the modesetting driver to it. Best-effort."""
    try:
        card_dirs = sorted(glob.glob("/sys/class/drm/card[0-9]"))
        candidates = [
            os.path.basename(path)
            for path in card_dirs
            if "v3d" not in os.path.realpath(os.path.join(path, "device"))
        ]
    except OSError:
        return
    if len(candidates) != 1:
        return
    conf_dir = "/etc/X11/xorg.conf.d"
    conf_path = os.path.join(conf_dir, "99-rem-kmsdevice.conf")
    conf_body = (
        'Section "Device"\n'
        '    Identifier "modesetting"\n'
        '    Driver "modesetting"\n'
        f'    Option "kmsdev" "/dev/dri/{candidates[0]}"\n'
        "EndSection\n"
    )
    try:
        if os.path.exists(conf_path) and open(conf_path).read() == conf_body:
            return
        os.makedirs(conf_dir, exist_ok=True)
        with open(conf_path, "w") as f:
            f.write(conf_body)
    except OSError:
        pass


def _chromium_binary():
    """Older Raspberry Pi OS ships "chromium-browser", newer ships "chromium"."""
    for name in ("chromium-browser", "chromium"):
        path = shutil.which(name)
        if path is not None:
            return path
    raise RuntimeError("Neither chromium-browser nor chromium is installed")


def local_hostname():
    return socket.gethostname()


def local_ip():
    """Best-effort LAN IP for the waiting screen. The UDP connect sends no
    packets; it just picks the default route's interface. Falls back to the
    host's own addresses on networks with no default route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()

    try:
        candidates = {
            info[4][0]
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        }
    except OSError:
        candidates = set()
    candidates.discard("127.0.0.1")
    return next(iter(candidates), None) or "unknown (check network connection)"


_RESOLUTION_DETECT_ATTEMPTS = 15
_RESOLUTION_DETECT_INTERVAL_SECONDS = 1.0


def _detect_screen_resolution(display=":0"):
    """(width, height) of the active mode via `xrandr`, retrying while X
    starts up. None if it can't be determined."""
    env = {**os.environ, "DISPLAY": display}
    for _ in range(_RESOLUTION_DETECT_ATTEMPTS):
        try:
            output = subprocess.run(
                ["xrandr"], env=env, capture_output=True, text=True, timeout=5
            ).stdout
        except (OSError, subprocess.SubprocessError):
            output = ""
        # Active mode line looks like "   1920x1080     59.94*+".
        for line in output.splitlines():
            if "*" not in line:
                continue
            match = re.match(r"^\s*(\d+)x(\d+)\b", line)
            if match:
                return int(match.group(1)), int(match.group(2))
        time.sleep(_RESOLUTION_DETECT_INTERVAL_SECONDS)
    return None


def detect_and_report_ui_scale(server_url, cookie, display=":0"):
    """Reports the screen's raw width/height to the server, which derives a
    UI scale from it unless an admin has set one manually. Blocks while X
    starts, so run it on a background thread. Best-effort."""
    resolution = _detect_screen_resolution(display)
    if resolution is None:
        print("Could not detect screen resolution -- leaving UI scale as-is")
        return
    width, height = resolution
    url = server_url.rstrip("/") + "/api/devices/me/detected-scale"
    payload = json.dumps({"width": width, "height": height}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Cookie": cookie},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except Exception as e:
        print("Could not report detected UI scale:", e)


def write_waiting_page(hostname, ip_address):
    html = _WAITING_PAGE_TEMPLATE.replace("@@HOSTNAME@@", hostname).replace("@@IP_ADDRESS@@", ip_address)
    fd, path = tempfile.mkstemp(prefix="rem-field-node-", suffix=".html")
    with open(fd, "w") as f:
        f.write(html)
    return path


class Chromium:
    """A single xinit + Chromium kiosk process."""

    def __init__(self):
        self._process = None

    def show(self, url):
        self.stop()
        _ensure_kms_device_pinned()
        # Without a window manager, --kiosk doesn't fill the screen, so size
        # the window explicitly from xrandr. This runs as the X client because
        # xrandr/xset need the server to be up.
        client_script = (
            # Disable screen blanking/DPMS so the display never goes dark.
            "xset s off; xset s noblank; xset -dpms; "
            "RES=$(xrandr | awk '/\\*/{print $1; exit}' | tr x ,); "
            "RES=${RES:-1920,1080}; "
            "exec " + shlex.quote(_chromium_binary())
            + " --window-position=0,0 --window-size=$RES "
            + " ".join(shlex.quote(arg) for arg in CHROMIUM_KIOSK_ARGS)
            + " " + shlex.quote(url)
        )
        self._process = subprocess.Popen(
            [
                "xinit",
                # Must be an absolute path, or xinit falls back to xterm.
                shutil.which("sh") or "/bin/sh", "-c", client_script,
                "--",
                *_XINIT_SERVER_ARGS,
            ]
        )

    def wait(self):
        if self._process is not None:
            self._process.wait()

    def poll(self):
        """None if still running, otherwise its exit code."""
        if self._process is None:
            return 0
        return self._process.poll()

    def stop(self):
        """Terminates xinit/Xorg/Chromium and waits for it to exit, so the
        next X server doesn't hit a stale /tmp/.X0-lock."""
        if self._process is None:
            return
        process = self._process
        self._process = None
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
