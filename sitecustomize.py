import os
import re
import sys
import time
import hmac
import hashlib
import base64
import struct
import json
import threading
import urllib.parse
import urllib.request
import urllib.error

# ============================================================
# EXISTING PSYCHO LANDING HOOK
# ============================================================
try:
    from flask import Flask, Response

    _original_run = Flask.run

    def _psycho_run(self, *args, **kwargs):
        try:
            script = os.path.basename(sys.argv[0] or "")
            if script == "bridge.py" and os.path.exists("landing.py"):
                with open("landing.py", "r", encoding="utf-8") as file:
                    source = file.read()

                marker = "PSYCHO_HTML = r'''"
                start = source.find(marker)
                if start >= 0:
                    start += len(marker)
                    end = source.find("'''", start)
                    if end >= 0:
                        html = source[start:end]

                        def psycho_home():
                            return Response(html, content_type="text/html; charset=utf-8")

                        self.view_functions["home"] = psycho_home
                        print("PSYCHO LANDING: ROOT ROUTE ENABLED", flush=True)
        except Exception as error:
            print(f"PSYCHO LANDING HOOK ERROR: {error}", flush=True)

        return _original_run(self, *args, **kwargs)

    Flask.run = _psycho_run
except Exception as error:
    print(f"PSYCHO SITE CUSTOMIZE LANDING ERROR: {error}", flush=True)

# ============================================================
# PSYCHO PHASE 2 — DHAN AUTHENTICATION HARDENING
# ============================================================
DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "").strip()
DHAN_TOTP_SECRET = os.environ.get("DHAN_TOTP_SECRET", "").strip()
DHAN_PIN = os.environ.get("DHAN_PIN", "").strip()
AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
MIN_TOKEN_REGEN_INTERVAL = 120
_auth_lock = threading.Lock()
_last_generation = 0.0
_last_token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
_auth_last_error = None
_auth_last_success = None
_token_expiry = None
os.environ.setdefault("DHAN_ACCESS_TOKEN", _last_token)


def _totp(secret, now=None):
    secret = "".join(secret.split()).replace("-", "").upper()
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret + padding, casefold=True)
    counter = int((now if now is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def _set_token(token, expiry=None):
    global _last_token, _token_expiry
    token = (token or "").strip()
    if not token:
        raise RuntimeError("Dhan returned an empty access token")
    _last_token = token
    _token_expiry = expiry
    os.environ["DHAN_ACCESS_TOKEN"] = token
    bridge = sys.modules.get("bridge")
    if bridge is not None:
        bridge.TOKEN = token
    return token


def _generate_token(force=False):
    global _last_generation, _auth_last_error, _auth_last_success
    if not (DHAN_CLIENT_ID and DHAN_PIN and DHAN_TOTP_SECRET):
        return None
    with _auth_lock:
        now = time.time()
        if not force and _last_token:
            return _last_token
        if now - _last_generation < MIN_TOKEN_REGEN_INTERVAL:
            return _last_token or None
        try:
            code = _totp(DHAN_TOTP_SECRET, now)
            query = urllib.parse.urlencode({"dhanClientId": DHAN_CLIENT_ID, "pin": DHAN_PIN, "totp": code})
            request = urllib.request.Request(f"{AUTH_URL}?{query}", data=b"", headers={"Accept": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            token = payload.get("accessToken")
            if not token:
                raise RuntimeError("response did not contain accessToken" f" (status={payload.get('status')}, error={payload.get('errorMessage') or payload.get('message')})")
            expiry = payload.get("expiryTime")
            _last_generation = now
            _set_token(token, expiry)
            _auth_last_error = None
            _auth_last_success = time.time()
            print(f"DHAN AUTH TOKEN GENERATED: expiry={expiry or 'unknown'}", flush=True)
            return token
        except Exception as exc:
            _last_generation = now
            _auth_last_error = f"{type(exc).__name__}: {exc}"
            print(f"DHAN AUTH GENERATION FAILED: {_auth_last_error}", flush=True)
            return None


_original_urlopen = urllib.request.urlopen


def _dhan_urlopen(request, *args, **kwargs):
    try:
        return _original_urlopen(request, *args, **kwargs)
    except urllib.error.HTTPError as exc:
        try:
            host = urllib.parse.urlparse(request.full_url).netloc
        except Exception:
            host = ""
        if exc.code != 401 or host != "api.dhan.co":
            raise
        try:
            exc.read()
        except Exception:
            pass
        try:
            exc.close()
        except Exception:
            pass
        token = _generate_token(force=True)
        if not token:
            raise
        return _original_urlopen(request, *args, **kwargs)


urllib.request.urlopen = _dhan_urlopen


def _auth_bootstrap():
    if DHAN_CLIENT_ID and DHAN_PIN and DHAN_TOTP_SECRET:
        token = _generate_token(force=True)
        if token:
            print("PSYCHO AUTH: TOTP authentication ready", flush=True)
        else:
            print("PSYCHO AUTH: TOTP configured but token generation failed", flush=True)
    elif _last_token:
        print("PSYCHO AUTH: using DHAN_ACCESS_TOKEN; automatic TOTP renewal is not configured", flush=True)
    else:
        print("PSYCHO AUTH: no DHAN_ACCESS_TOKEN and no TOTP credentials; data collection will remain AUTH_FAILED", flush=True)


def _data_is_valid(snapshot):
    if not isinstance(snapshot, dict):
        return False
    market = snapshot.get("market") or {}
    current = market.get("current_session") or {}
    if current.get("last_price") is not None:
        return True
    tfs = market.get("timeframes") or {}
    return any(bool(tfs.get(tf)) for tf in ("1M", "5M", "15M", "1H", "1D", "1W"))


def _cleanup_failed_snapshot(config):
    for key in ("market_file", "futures_file", "option_file", "snapshot_file"):
        path = config.get(key)
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as exc:
            print(f"DATA CLEANUP ERROR {path}: {exc}", flush=True)


def _patch_bridge():
    while "bridge" not in sys.modules:
        time.sleep(0.05)
    bridge = sys.modules["bridge"]
    if getattr(bridge, "_psycho_hardening_applied", False):
        return
    original_build = bridge.build_instrument

    def guarded_build(key, config, session_date):
        snapshot = original_build(key, config, session_date)
        if not _data_is_valid(snapshot):
            _cleanup_failed_snapshot(config)
            raise RuntimeError(f"{config.get('display_name', key)}: NO VALID MARKET DATA (DHAN authentication/data acquisition failed)")
        return snapshot

    bridge.build_instrument = guarded_build

    def auth_status_view():
        from flask import jsonify
        configured = bool(DHAN_CLIENT_ID and DHAN_PIN and DHAN_TOTP_SECRET)
        return jsonify({
            "service": "PSYCHO MARKET BRIDGE",
            "authentication": {
                "status": "AUTO_TOTP_READY" if configured else ("TOKEN_PRESENT" if _last_token else "AUTH_NOT_CONFIGURED"),
                "client_id_present": bool(DHAN_CLIENT_ID),
                "access_token_present": bool(_last_token),
                "totp_configured": bool(DHAN_TOTP_SECRET),
                "pin_configured": bool(DHAN_PIN),
                "last_success": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_auth_last_success)) if _auth_last_success else None,
                "last_error": _auth_last_error,
                "token_expiry": _token_expiry,
            },
        })
    bridge.app.add_url_rule("/auth-status", endpoint="psycho_auth_status", view_func=auth_status_view, methods=["GET"])

    if bridge.app.view_functions.get("bridge_status"):
        def hardened_bridge_status():
            from flask import jsonify
            status = {}
            for key, config in bridge.INSTRUMENTS.items():
                snap = bridge.read_json_file(config["snapshot_file"])
                valid = _data_is_valid(snap)
                market = (snap or {}).get("market") if isinstance(snap, dict) else {}
                status[key] = {
                    "data_ready": valid,
                    "market_file_exists": os.path.exists(config["market_file"]),
                    "option_file_exists": os.path.exists(config["option_file"]),
                    "futures_file_exists": os.path.exists(config["futures_file"]),
                    "snapshot_file_exists": os.path.exists(config["snapshot_file"]),
                    "session_date": (snap or {}).get("session_date") if isinstance(snap, dict) else None,
                    "snapshot_generated_at": (snap or {}).get("snapshot_generated_at") if isinstance(snap, dict) else None,
                    "last_price": ((market or {}).get("current_session") or {}).get("last_price"),
                }
            any_ready = any(item["data_ready"] for item in status.values())
            return jsonify({
                "service": "PSYCHO MARKET BRIDGE",
                "server": "ONLINE",
                "source": "DHAN",
                "server_time": bridge.iso_now(),
                "market": bridge.market_status(),
                "authentication": {"status": "AUTO_TOTP_READY" if (DHAN_CLIENT_ID and DHAN_PIN and DHAN_TOTP_SECRET) else ("TOKEN_PRESENT" if _last_token else "AUTH_FAILED")},
                "data_health": "READY" if any_ready else "FAILED",
                "refresh_target_seconds": bridge.REFRESH_INTERVAL_SECONDS,
                "instruments": status,
            })
        bridge.app.view_functions["bridge_status"] = hardened_bridge_status

    bridge._psycho_hardening_applied = True
    print("PSYCHO HARDENING: bridge data-health/auth diagnostics applied", flush=True)


_auth_bootstrap()
threading.Thread(target=_patch_bridge, daemon=True, name="psycho-bridge-hardening").start()


def _renewal_worker():
    if not (DHAN_CLIENT_ID and DHAN_PIN and DHAN_TOTP_SECRET):
        return
    while True:
        time.sleep(23 * 60 * 60)
        _generate_token(force=True)

threading.Thread(target=_renewal_worker, daemon=True, name="psycho-token-renewal").start()

# ============================================================
# PSYCHO ENGINE COMPATIBILITY — SPLIT OPTION CHAIN ADAPTER
# ============================================================
# Phase 2 now exposes market data and option-chain data through separate
# endpoints. The older opportunity/hunter/9301030/10301130 engines expect
# option_chain to be embedded in /nifty-live and /banknifty-live. Merge it
# transparently at runtime so their locked strategy logic remains unchanged.

_COMPAT_BRIDGE_HOST = "psycho-market-bridge.onrender.com"
_COMPAT_ORIGINAL_URLOPEN = urllib.request.urlopen


class _PsychoBufferedResponse:
    def __init__(self, body, original):
        self._body = __import__("io").BytesIO(body)
        self.headers = getattr(original, "headers", None)
        self.url = getattr(original, "url", None)
        self.status = getattr(original, "status", None)
        self.code = getattr(original, "code", None)
        self.reason = getattr(original, "reason", None)

    def read(self, *args, **kwargs):
        return self._body.read(*args, **kwargs)

    def getcode(self):
        return self.code

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._body.close()
        return False


def _psycho_engine_urlopen(request, *args, **kwargs):
    try:
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return _COMPAT_ORIGINAL_URLOPEN(request, *args, **kwargs)

    if parsed.netloc != _COMPAT_BRIDGE_HOST or parsed.path not in ("/nifty-live", "/banknifty-live"):
        return _COMPAT_ORIGINAL_URLOPEN(request, *args, **kwargs)

    original = _COMPAT_ORIGINAL_URLOPEN(request, *args, **kwargs)
    raw = original.read()
    try:
        market = json.loads(raw.decode("utf-8"))
    except Exception:
        return _PsychoBufferedResponse(raw, original)

    if isinstance(market, dict) and "option_chain" not in market:
        option_path = "/nifty-option-chain" if parsed.path == "/nifty-live" else "/banknifty-option-chain"
        try:
            option_req = urllib.request.Request(
                f"https://{_COMPAT_BRIDGE_HOST}{option_path}",
                headers={"Accept": "application/json", "User-Agent": "PSYCHO-ENGINE-COMPAT/1.0"},
            )
            with _COMPAT_ORIGINAL_URLOPEN(option_req, timeout=15) as option_resp:
                option = json.loads(option_resp.read().decode("utf-8"))
            if isinstance(option, dict):
                market["option_chain"] = option
                market["compatibility"] = {"option_chain_merged": True, "source": option_path}
                raw = json.dumps(market, ensure_ascii=False).encode("utf-8")
        except Exception as exc:
            market["compatibility"] = {"option_chain_merged": False, "option_chain_error": type(exc).__name__}
            raw = json.dumps(market, ensure_ascii=False).encode("utf-8")

    return _PsychoBufferedResponse(raw, original)


urllib.request.urlopen = _psycho_engine_urlopen
print("PSYCHO ENGINE COMPAT: split option-chain adapter enabled", flush=True)
