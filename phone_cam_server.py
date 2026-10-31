"""Phone-as-camera server: a lightweight replacement for the IP Webcam app.

Run this on the PC, open the printed https URL once in the phone's Chrome, tap
Start. The page grabs the rear camera and POSTs small JPEGs a few times per
second (much cooler than a full-rate MJPEG server on the phone). This server
re-serves the latest frame as a plain-HTTP MJPEG stream on localhost, so the
scanner needs no changes:

    ./venv/bin/python phone_cam_server.py
    ./venv/bin/python live_scanner.py http://127.0.0.1:8081/video

Phones only allow camera access over HTTPS, so a self-signed cert is generated
on first run (phone_cam.pem/.key) -- accept the one-time browser warning.
Frames are normalized to landscape (rotated if the phone sends portrait) to
match what IP Webcam delivered, since live_scanner.py rotates 90 CW itself.
"""
import json
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

HTTPS_PORT = 8443  # phone -> PC
VIDEO_PORT = 8081  # scanner <- PC, localhost only
CERT, KEY = Path("phone_cam.pem"), Path("phone_cam.key")

PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07090f">
<title>TCG Catalog - phone camera</title>
<style>
:root{--bg:#07090f;--card:rgba(22,26,37,.82);--line:rgba(255,255,255,.09);--text:#e9edf5;--muted:#8d97ab;
 --accent:#8b7cff;--accent2:#38d9f5;--good:#3ddc97;--warn:#ffb347}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);font:16px/1.4 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body{min-height:100vh;padding:max(14px,env(safe-area-inset-top)) 14px 24px;
 background:radial-gradient(70% 40% at 0% 0%,rgba(124,92,255,.32),transparent 70%),radial-gradient(60% 35% at 100% 0%,rgba(34,211,238,.20),transparent 70%),radial-gradient(70% 40% at 100% 100%,rgba(236,72,153,.14),transparent 70%),linear-gradient(160deg,#0b0c1d,#070912 50%,#0a0918);background-attachment:fixed}
header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.logo{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:grid;place-items:center;font-weight:800;color:#0b0d14}
header b{font-size:15px;letter-spacing:.01em}header small{display:block;color:var(--muted);font-size:12px}
.frame{position:relative;border-radius:18px;overflow:hidden;border:1px solid var(--line);background:#000;aspect-ratio:16/9}
video{width:100%;height:100%;object-fit:contain;display:block}
#st{position:absolute;left:10px;bottom:10px;padding:5px 10px;border-radius:999px;background:rgba(0,0,0,.6);backdrop-filter:blur(6px);font-size:12.5px;color:#cfd6e4;border:1px solid var(--line)}
#lum{position:absolute;right:10px;bottom:10px;padding:5px 10px;border-radius:999px;background:rgba(0,0,0,.6);font-size:12px;color:var(--muted)}
#lum:empty{display:none}
.hint{color:var(--muted);font-size:13.5px;text-align:center;margin:12px 6px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:14px;padding:13px 10px;font-weight:600;width:100%}
button:active{transform:scale(.98)}button:disabled{opacity:.45}
#go{margin-top:14px;padding:17px;font-size:18px;border:0;color:#0b0d14;background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 10px 30px rgba(139,124,255,.35)}
#go:disabled{opacity:1;background:rgba(61,220,151,.14);color:var(--good);border:1px solid rgba(61,220,151,.35);box-shadow:none}
.row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
button.lit{background:rgba(255,213,74,.18);border-color:#ffd54a;color:#ffe48a}
#bright{margin-top:10px;padding:12px 14px;border-radius:14px;background:var(--card);border:1px solid var(--line)}
#brLabel{font-size:12.5px;color:var(--muted);margin-bottom:8px}
#br{width:100%;accent-color:var(--accent)}
#dim{display:none;position:fixed;inset:0;background:#000;z-index:9;color:#3a3f4b;align-items:center;justify-content:center;font-size:14px}
</style></head><body>
<header><div class="logo">&#9889;</div><div><b>TCG Catalog</b><small>Phone camera</small></div></header>
<div class="frame"><video id="v" playsinline muted></video><div id="st">Tap Start with the Slinger empty</div><div id="lum"></div></div>
<button id="go">Start camera</button>
<div class="row"><button id="dimbtn">Dim screen</button><button id="relock">Relock</button><button id="torch" disabled>Torch</button></div>
<div id="bright" style="display:none"><div id="brLabel"></div><input id="br" type="range"></div>
<p class="hint">Keep this page open and the phone awake. You'll hear a ping for every card added &mdash; two low beeps mean it's a duplicate.</p>
<div id="dim">streaming &middot; tap to wake</div>
<script>
const v=document.getElementById('v'),st=document.getElementById('st'),dim=document.getElementById('dim');
const FPS=6,WIDTH=1920,QUALITY=0.9;
let busy=false,sent=0,pingSeq=-1,actx=null;
// Success ping: rising two-tone = card added (feed the next card); two low beeps = duplicate (dupes pile).
function tone(f,t0,d,vol){const o=actx.createOscillator(),g=actx.createGain();o.type='sine';o.frequency.value=f;
  g.gain.setValueAtTime(0,actx.currentTime+t0);g.gain.linearRampToValueAtTime(vol||0.5,actx.currentTime+t0+0.01);
  g.gain.exponentialRampToValueAtTime(0.0001,actx.currentTime+t0+d);o.connect(g);g.connect(actx.destination);
  o.start(actx.currentTime+t0);o.stop(actx.currentTime+t0+d+0.02);}
function pingPhone(dup){try{if(!actx)return;if(actx.state==='suspended')actx.resume();
  if(dup){tone(330,0,0.12);tone(330,0.16,0.12);if(navigator.vibrate)navigator.vibrate([60,60,60]);}
  else{tone(880,0,0.1);tone(1320,0.09,0.18);if(navigator.vibrate)navigator.vibrate(70);}}catch(e){}}
// Let auto exposure/white balance settle on the empty well, then freeze them (and focus)
// so changing card colours or room light doesn't swing the picture. 'Relock' redoes this
// after you adjust the lights.
async function lockCam(first){
  const tr=window.track,c=tr.getCapabilities?tr.getCapabilities():{},got=[];
  const set=async(o,n)=>{try{await tr.applyConstraints({advanced:[o]});got.push(n);}catch(e){}};
  const has=(k,v)=>c[k]&&c[k].includes&&c[k].includes(v);
  if(has('exposureMode','continuous'))await set({exposureMode:'continuous'},'');
  if(has('whiteBalanceMode','continuous'))await set({whiteBalanceMode:'continuous'},'');
  if(has('focusMode','continuous'))await set({focusMode:'continuous'},'');
  st.textContent='Measuring light\\u2026';
  await new Promise(r=>setTimeout(r,2500));
  const s=tr.getSettings();
  if(has('exposureMode','manual')&&s.exposureTime)await set({exposureMode:'manual',exposureTime:s.exposureTime,iso:s.iso},'exposure');
  if(has('whiteBalanceMode','manual')&&s.colorTemperature)await set({whiteBalanceMode:'manual',colorTemperature:s.colorTemperature},'white balance');
  if(has('focusMode','manual')&&s.focusDistance)await set({focusMode:'manual',focusDistance:s.focusDistance},'focus');
  st.textContent='Locked: '+(got.filter(x=>x).join(', ')||'auto (this phone cannot lock)');
}
document.getElementById('dimbtn').onclick=()=>{dim.style.display='flex'};
dim.onclick=()=>{dim.style.display='none'};
document.getElementById('relock').onclick=async()=>{if(window.track){await lockCam();initBrightness();}};
// Torch (phone flashlight) probe + manual toggle. Chrome/Android exposes it as a track
// capability on the rear camera; not every phone/browser does, so say so plainly.
// Brightness slider: Android Chrome can't set the flash LED strength (torch is on/off only),
// so this controls how bright the camera records instead -- exposure time when manual
// exposure is available (log scale), else exposure compensation. Works with the torch on or off.
function initBrightness(){
  const tr=window.track,c=tr.getCapabilities?tr.getCapabilities():{},wrap=document.getElementById('bright'),
        sl=document.getElementById('br'),lab=document.getElementById('brLabel');
  const apply=o=>tr.applyConstraints({advanced:[o]}).catch(e=>{lab.textContent='brightness failed: '+e.name});
  const et=c.exposureTime,s0=tr.getSettings();
  if(et&&et.max>et.min&&s0.exposureMode==='manual'){
    // centred on the locked exposure (slider 50), +/-3 stops either side, clamped to what the phone allows
    const base=s0.exposureTime||et.min,lo=Math.log(Math.max(et.min,base/8)),hi=Math.log(Math.min(et.max,base*8));
    sl.min=0;sl.max=100;sl.step=1;sl.value=50;
    const go=async()=>{const v=Math.exp(lo+(hi-lo)*sl.value/100);
      await apply({exposureMode:'manual',exposureTime:v});
      await new Promise(r=>setTimeout(r,250));
      lab.textContent='Image brightness \\u00B7 exposure '+(tr.getSettings().exposureTime||v.toFixed(1));};
    sl.oninput=go;lab.textContent='Image brightness (camera exposure, not the torch)';
  }else if(c.exposureCompensation&&c.exposureCompensation.max>c.exposureCompensation.min){
    const ec=c.exposureCompensation;
    sl.min=ec.min;sl.max=ec.max;sl.step=ec.step||0.1;sl.value=s0.exposureCompensation||0;
    sl.oninput=()=>{lab.textContent='Image brightness \\u00B7 '+sl.value;apply({exposureCompensation:parseFloat(sl.value)});};
    lab.textContent='Image brightness (camera exposure, not the torch)';
  }else{wrap.style.display='none';return;}
  wrap.style.display='block';
}
let torchOn=false;
function initTorch(){
  const b=document.getElementById('torch');
  const c=window.track.getCapabilities?window.track.getCapabilities():{};
  if(!c.torch){b.textContent='No torch';b.disabled=true;return;}
  b.disabled=false;b.textContent='Torch';
  const setTorch=async on=>{
    try{await window.track.applyConstraints({advanced:[{torch:on}]});
      torchOn=on;b.textContent=torchOn?'Torch on':'Torch';b.classList.toggle('lit',torchOn);}
    catch(e){b.textContent='Torch error';}
  };
  b.onclick=()=>setTorch(!torchOn);
  // The scanner can pulse the torch (quick on/off helps recognise cards that don't match straight away):
  // poll the server for torch commands and apply any new one.
  let seq=-1;
  setInterval(async()=>{try{const c=await (await fetch('/cmd',{cache:'no-store'})).json();
    if(seq<0){seq=c.seq;pingSeq=c.ping;return;}
    if(c.seq!==seq){seq=c.seq;if(c.torch!==torchOn)setTorch(c.torch);}
    if(c.ping!==pingSeq){pingSeq=c.ping;pingPhone(c.dup);}}catch(e){}},120);
}
document.getElementById('go').onclick=async()=>{
  if(window.__started)return;window.__started=true;const gb=document.getElementById('go');gb.disabled=true;gb.textContent='Camera running';
  try{actx=new (window.AudioContext||window.webkitAudioContext)();await actx.resume();}catch(e){}
  let stream;try{stream=await navigator.mediaDevices.getUserMedia({audio:false,
    video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}}});}
  catch(e){window.__started=false;gb.disabled=false;gb.textContent='Start camera';st.textContent='Camera error: '+e.name+' (allow camera access)';return;}
  v.srcObject=stream;await v.play();
  const tr=stream.getVideoTracks()[0];
  window.track=tr;
  await lockCam(true);
  initTorch();
  initBrightness();
  try{await navigator.wakeLock.request('screen');}catch(e){}
  const cv=document.createElement('canvas'),lc=document.createElement('canvas');lc.width=32;lc.height=18;
  setInterval(()=>{if(!v.videoWidth)return;const x=lc.getContext('2d');x.drawImage(v,0,0,32,18);
    const d=x.getImageData(0,0,32,18).data;let t=0;for(let i=0;i<d.length;i+=4)t+=d[i]*.3+d[i+1]*.59+d[i+2]*.11;
    document.getElementById('lum').textContent='light '+Math.round(t/(d.length/4));},500);
  setInterval(()=>{
    if(busy||!v.videoWidth)return;
    const s=Math.min(1,WIDTH/Math.max(v.videoWidth,v.videoHeight));
    cv.width=v.videoWidth*s;cv.height=v.videoHeight*s;
    cv.getContext('2d').drawImage(v,0,0,cv.width,cv.height);
    busy=true;
    cv.toBlob(b=>fetch('/frame',{method:'POST',body:b}).then(()=>{sent++;st.textContent='\\u25CF Streaming \\u00B7 '+sent+' frames'}).catch(()=>{st.textContent='send failed'}).finally(()=>{busy=false}),'image/jpeg',QUALITY);
  },1000/FPS);
};
</script></body></html>"""

_latest = {"jpg": None, "n": 0, "t": 0.0}
_cmd = {"torch": False, "seq": 0, "ping": 0, "dup": False}  # torch command for the phone page (set by the scanner via POST :8081/torch)
_cond = threading.Condition()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    finally:
        s.close()


CERT_IP = Path("phone_cam.ip")


def ensure_cert(ip):
    """Self-signed certificate for the phone page (phones only allow the camera over HTTPS).
    Regenerated automatically if this computer's network address changed since it was made."""
    if CERT.exists() and KEY.exists() and CERT_IP.exists() and CERT_IP.read_text().strip() == ip:
        return
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
             "-keyout", str(KEY), "-out", str(CERT), "-subj", "/CN=phone-cam",
             "-addext", f"subjectAltName=IP:{ip},IP:127.0.0.1"],
            check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        _cert_with_cryptography(ip)   # Windows / no openssl installed
    CERT_IP.write_text(ip)


def _cert_with_cryptography(ip):
    import datetime, ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phone-cam")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip)),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def normalize(jpg_bytes):
    """Decode, rotate portrait -> landscape, re-encode. None if undecodable."""
    img = cv2.imdecode(np.frombuffer(jpg_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    if img.shape[0] > img.shape[1]:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else None


class PhoneHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/cmd"):
            body = json.dumps(_cmd).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        jpg = normalize(body)
        if jpg:
            with _cond:
                _latest["jpg"], _latest["n"], _latest["t"] = jpg, _latest["n"] + 1, time.time()
                _cond.notify_all()
        self.send_response(204)
        self.end_headers()


class VideoHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        # Scanner -> phone: POST /torch?on=1 (or on=0). The phone page polls /cmd and applies it.
        if self.path.startswith("/ping"):
            # Scanner -> phone: a card was just added (dup=1 if it's a duplicate). The page plays a tone.
            _cmd["ping"] += 1
            _cmd["dup"] = "dup=1" in self.path
            self.send_response(204)
            self.end_headers()
        elif self.path.startswith("/torch"):
            _cmd["torch"] = "on=1" in self.path
            _cmd["seq"] += 1
            self.send_response(204)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            # seconds since the phone last sent a frame (None = never) -- lets the control panel show "phone streaming"
            body = json.dumps({"frames": _latest["n"], "age": (time.time() - _latest["t"]) if _latest["t"] else None}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/shot.jpg"):
            with _cond:
                jpg = _latest["jpg"]
            if jpg is None:
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.end_headers()
            self.wfile.write(jpg)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        last = -1
        try:
            while True:
                with _cond:
                    _cond.wait_for(lambda: _latest["n"] != last, timeout=5)
                    jpg, last = _latest["jpg"], _latest["n"]
                if jpg is None:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg))
                self.wfile.write(jpg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ip = lan_ip()
    ensure_cert(ip)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    https = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), PhoneHandler)
    https.socket = ctx.wrap_socket(https.socket, server_side=True)
    video = ThreadingHTTPServer(("127.0.0.1", VIDEO_PORT), VideoHandler)
    for s in (https, video):
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"On the phone, open:  https://{ip}:{HTTPS_PORT}   (accept the cert warning, tap Start)")
    print(f"Scanner source:      http://127.0.0.1:{VIDEO_PORT}/video", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
