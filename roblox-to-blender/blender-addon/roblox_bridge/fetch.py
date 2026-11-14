"""Fallback downloader for assets Studio's EditableMesh/EditableImage refuse to open.

Roblox's asset delivery API now generally needs authentication. Provide ONE of:
  * an Open Cloud API key (recommended; create.roblox.com > Open Cloud > API Keys,
    created on your PERSONAL account (not a group) with the "legacy-asset" API
    system and the "legacy-asset:manage" scope - "asset:read" returns 403), or
  * your .ROBLOSECURITY cookie (works, but treat it like a password).
Both are stored only in Blender's add-on preferences on this machine.
Unauthenticated requests are tried as well, in case Roblox still serves them.
"""

import gzip
import json
import re
import time
import urllib.error
import urllib.request

UA = "RobloxBlenderBridge/1.0"


def _get(url, headers, timeout=20, tries=4):
    """GET with retries: Roblox answers 429 (rate limited) or 5xx when busy, and a stuck
    connection times out; both are retried with the server's Retry-After or a growing pause."""
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding", "") == "gzip" or data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            if attempt == tries - 1 or not (e.code == 429 or e.code >= 500):
                raise
            try:
                wait = float(e.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            time.sleep(min(30.0, max(wait, 1.5 * 2 ** attempt)))
        except (TimeoutError, OSError) as e:
            if attempt == tries - 1 or isinstance(e, urllib.error.HTTPError):
                raise
            time.sleep(1.5 * 2 ** attempt)


def fetch_asset(asset_id, cookie="", api_key=""):
    asset_id = str(int(asset_id))
    errors = []
    attempts = []
    if api_key:
        attempts.append(("open cloud", "https://apis.roblox.com/asset-delivery-api/v1/assetId/" + asset_id, {"x-api-key": api_key}))
    if cookie:
        c = cookie.strip()
        if not c.startswith(".ROBLOSECURITY="):
            c = ".ROBLOSECURITY=" + c
        attempts.append(("cookie", "https://assetdelivery.roblox.com/v1/asset/?id=" + asset_id, {"Cookie": c}))
    attempts.append(("anonymous", "https://assetdelivery.roblox.com/v1/asset/?id=" + asset_id, {}))

    for label, url, headers in attempts:
        try:
            data = _get(url, headers)
            # Open Cloud returns JSON with a CDN location
            if data[:1] == b"{":
                info = json.loads(data.decode("utf-8"))
                loc = info.get("location") or (info.get("locations") or [{}])[0].get("location")
                if not loc:
                    raise ValueError("no location in response: %s" % str(info)[:120])
                data = _get(loc, {})
            return data
        except urllib.error.HTTPError as e:
            errors.append("%s: HTTP %d" % (label, e.code))
        except Exception as e:
            errors.append("%s: %s" % (label, e))
    hint = "" if (cookie or api_key) else " (add an API key or cookie in the add-on preferences)"
    raise RuntimeError("; ".join(errors) + hint)


def check_credentials(cookie="", api_key=""):
    """-> (ok, message). Asks for a non-existent asset: 404 means 'authenticated',
    401/403 means the key/cookie was rejected."""
    if not (cookie or api_key):
        return False, "No API key or cookie entered"
    tests = []
    if api_key:
        tests.append(("API key", "https://apis.roblox.com/asset-delivery-api/v1/assetId/1", {"x-api-key": api_key}))
    if cookie:
        c = cookie.strip()
        if not c.startswith(".ROBLOSECURITY="):
            c = ".ROBLOSECURITY=" + c
        tests.append(("cookie", "https://assetdelivery.roblox.com/v1/asset/?id=1", {"Cookie": c}))
    msgs, good = [], False
    for label, url, headers in tests:
        try:
            _get(url, headers, timeout=15)
            good = True
            msgs.append("%s works" % label)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400, 409):
                good = True
                msgs.append("%s works" % label)
            elif e.code in (401, 403):
                hint = " (needs scope legacy-asset:manage on a personal-account key, and your IP allowed)" if label == "API key" else ""
                msgs.append("%s rejected: HTTP %d%s" % (label, e.code, hint))
            else:
                msgs.append("%s: HTTP %d" % (label, e.code))
        except Exception as e:
            msgs.append("%s: %s" % (label, e))
    return good, "; ".join(msgs)


def fetch_thumbnail(asset_id, size="700x700"):
    """NExport's fallback: Roblox's public thumbnail service needs no login. For Image and
    Decal assets the thumbnail IS the picture (max 700px, letterboxed to a square).
    Mesh/model thumbnails are only renders and are refused."""
    url = ("https://thumbnails.roblox.com/v1/assets?assetIds=%d&returnPolicy=PlaceHolder&size=%s&format=Png&isCircular=false"
           % (int(asset_id), size))
    info = json.loads(_get(url, {}).decode("utf-8"))
    d = (info.get("data") or [{}])[0]
    img = d.get("imageUrl") or ""
    if d.get("state") != "Completed" or not re.search(r"/(Image|Decal)/", img):
        raise ValueError("no image thumbnail (%s)" % (d.get("state") or "none"))
    return _get(img, {})


def fetch_image(asset_id, cookie="", api_key="", _depth=0):
    """Download an image -> (bytes, how). Follows Decal/rbxm wrappers to the real image id.
    how = "full" (original file) or "thumbnail" (public 700px thumbnail, no login needed)."""
    try:
        data = fetch_asset(asset_id, cookie, api_key)
    except Exception as e:
        try:
            return fetch_thumbnail(asset_id), "thumbnail"
        except Exception as e2:
            raise RuntimeError("%s; thumbnail: %s" % (e, e2))
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:2] == b"BM":
        return data, "full"
    if _depth < 2:
        text = data[:200000].decode("utf-8", "ignore")
        m = re.search(r"(?:rbxassetid://|[?&]id=)(\d+)", text)
        if m and m.group(1) != str(asset_id):
            return fetch_image(m.group(1), cookie, api_key, _depth + 1)
    raise ValueError("asset %s is not an image" % asset_id)
