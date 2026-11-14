"""Local HTTP receiver for the Roblox Studio plugin.

Runs in a background thread. It only writes files into a session folder
(the "dump"); the Blender scene is built on the main thread from that
folder, so a dump can always be re-imported later without Studio.

Session folder layout:
    scene.json
    meshes/<key>.rbm        binary mesh (see meshio.read_rbm)
    textures/<key>.png      exact RGBA pixels from EditableImage (or downloaded file)
    baked/                  composited / rebaked textures written by the builder
    report.txt

Asset cache: <export root>/_cache/meshes/a<id>.rbm and _cache/textures/a<id>.<ext>. An asset id's
content never changes, so each one is downloaded (or received from Studio) once per machine and
hard-linked into later sessions. Studio asks /have first and doesn't send what's cached.
"""

import base64
import concurrent.futures
import json
import os
import queue
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import builtin_mats, fetch, meshio, pngio

PROTOCOL = 1
MAX_BODY = 4 * 1024 * 1024
_KEY_RE = re.compile(r"[^A-Za-z0-9_\-]")


def safe_key(k):
    k = _KEY_RE.sub("_", str(k))[:120]
    if not k:
        raise ValueError("empty key")
    return k


class Bridge:
    def __init__(self):
        self.httpd = None
        self.thread = None
        self.port = None
        self.lock = threading.Lock()
        self.sessions = {}  # sid -> dict(dir, state, message, warnings)
        self.partials = {}  # (sid, kind, key) -> {index: bytes}
        self.jobs = queue.Queue()  # session dirs ready to build (main thread)
        # set by the add-on
        self.export_root = os.path.expanduser("~/RobloxExports")
        self.addon_version = "?"
        self.blender_version = "?"
        self.creds = lambda: ("", "")  # (cookie, open-cloud api key)

    # ---------------------------------------------------------------- control
    @property
    def running(self):
        return self.httpd is not None

    def start(self, port):
        if self.httpd:
            return
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                try:
                    u = urlparse(self.path)
                    if u.path == "/ping":
                        return self._send(200, bridge.ping())
                    if u.path == "/status":
                        sid = parse_qs(u.query).get("session", [""])[0]
                        return self._send(200, bridge.status(sid))
                    self._send(404, {"error": "not found"})
                except Exception as e:
                    self._send(500, {"error": str(e)})

            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if n <= 0 or n > MAX_BODY:
                        return self._send(413, {"error": "bad body size %d" % n})
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                    path = urlparse(self.path).path
                    if path == "/begin":
                        return self._send(200, bridge.begin(body))
                    if path == "/blob":
                        return self._send(200, bridge.blob(body))
                    if path == "/commit":
                        return self._send(200, bridge.commit(body))
                    if path == "/have":
                        return self._send(200, bridge.have(body))
                    if path == "/continue":
                        return self._send(200, bridge.resume(body))
                    self._send(404, {"error": "not found"})
                except KeyError as e:
                    self._send(400, {"error": "missing field %s" % e})
                except ValueError as e:
                    self._send(400, {"error": str(e)})
                except Exception as e:
                    traceback.print_exc()
                    self._send(500, {"error": str(e)})

        # localhost only, never exposed to the network
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.port = port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.httpd = None
        self.thread = None

    # -------------------------------------------------------------- endpoints
    def ping(self):
        return {
            "ok": True,
            "protocol": PROTOCOL,
            "addon": self.addon_version,
            "blender": self.blender_version,
            "export_root": self.export_root,
            "can_download": any(bool(c) for c in self.creds()),
            "cache": True,
        }

    @property
    def cache_dir(self):
        return os.path.join(self.export_root, "_cache")

    def have(self, body):
        """Which of these asset keys are cached already: {"meshes": [...], "textures": [...]}"""
        out = {}
        for kind, folder in (("meshes", "meshes"), ("textures", "textures")):
            keys = [safe_key(k) for k in (body.get(kind) or [])][:20000]
            out[kind] = [k for k in keys if _cached(self.cache_dir, folder, k)]
        return out

    def _session(self, sid):
        with self.lock:
            s = self.sessions.get(sid)
        if not s:
            raise ValueError("unknown session (did Blender restart?)")
        return s

    def set_status(self, sid, state=None, message=None, warnings=None):
        with self.lock:
            s = self.sessions.get(sid)
            if not s:
                return
            if state:
                s["state"] = state
            if message is not None:
                s["message"] = message
            if warnings is not None:
                s["warnings"] = list(warnings)

    def status(self, sid):
        with self.lock:
            s = self.sessions.get(sid)
            if not s:
                return {"state": "unknown", "message": "unknown session"}
            st = {"state": s["state"], "message": s["message"], "warnings": s.get("warnings", [])[:200]}
            if s.get("need"):
                st["need"] = s["need"]
            return st

    def begin(self, body):
        if body.get("protocol") != PROTOCOL:
            raise ValueError("protocol mismatch")
        name = safe_key(body.get("name") or "Export")[:60]
        stamp = time.strftime("%Y%m%d_%H%M%S")
        d = os.path.join(self.export_root, "%s_%s" % (name, stamp))
        for sub in ("meshes", "textures", "baked"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        sid = uuid.uuid4().hex
        with self.lock:
            self.sessions[sid] = {"dir": d, "state": "receiving", "message": "receiving data", "warnings": []}
        return {"session": sid, "dir": d}

    def blob(self, body):
        sid = body["session"]
        s = self._session(sid)
        kind = body["kind"]
        if kind not in ("mesh", "texture", "scene"):
            raise ValueError("bad kind")
        key = safe_key(body["key"])
        index, total = int(body["index"]), int(body["total"])
        if not (0 <= index < total <= 100000):
            raise ValueError("bad chunk index")
        chunk = base64.b64decode(body["data"], validate=True)
        pk = (sid, kind, key)
        with self.lock:
            parts = self.partials.setdefault(pk, {})
            parts[index] = chunk
            complete = len(parts) == total
            if complete:
                data = b"".join(parts[i] for i in range(total))
                del self.partials[pk]
        if not complete:
            return {"ok": True, "complete": False}

        d = s["dir"]
        if kind == "mesh":
            meshio.read_rbm(data)  # validate now so errors reach Studio
            _write(os.path.join(d, "meshes", key + ".rbm"), data)
        elif kind == "texture":
            meta = body.get("meta") or {}
            w, h = int(meta["w"]), int(meta["h"])
            if w <= 0 or h <= 0 or len(data) != w * h * 4:
                raise ValueError("texture %s: %d bytes for %dx%d" % (key, len(data), w, h))
            _write(os.path.join(d, "textures", key + ".png"), pngio.encode_rgba(w, h, data))
            tp = os.path.join(d, "textures", "thumbnails.json")
            if os.path.exists(tp):   # Studio's full image replaces a downloaded thumbnail
                with self.lock:
                    with open(tp) as f:
                        th = [k for k in json.load(f) if k != key]
                    with open(tp, "w") as f:
                        json.dump(th, f)
        else:
            json.loads(data.decode("utf-8"))
            _write(os.path.join(d, "scene.json"), data)
        return {"ok": True, "complete": True}

    def commit(self, body):
        sid = body["session"]
        s = self._session(sid)
        if not os.path.exists(os.path.join(s["dir"], "scene.json")):
            raise ValueError("scene.json not received")
        # Studio that can read assets itself (EditableMesh/Image) gets asked for what failed to download
        s["studio_fallback"] = bool(body.get("fallback"))
        self.set_status(sid, "downloading", "fetching assets")
        threading.Thread(target=self._prepare, args=(sid,), daemon=True).start()
        return {"ok": True}

    def resume(self, body):
        """Studio sent what it could of the assets we asked for (state "need"): build now."""
        sid = body["session"]
        s = self._session(sid)
        with self.lock:
            s.pop("need", None)       # so the next status poll doesn't ask Studio again
            s["state"] = "downloading"
        ev = s.get("resume")
        if ev:
            ev.set()
        return {"ok": True}

    # ----------------------------------------------------------- background
    def _prepare(self, sid):
        s = self._session(sid)
        try:
            prog = lambda m: self.set_status(sid, message=m)
            notes, missing = prepare_downloads(s["dir"], self.creds(), prog, self.cache_dir)
            if missing and s.get("studio_fallback"):
                # ask Studio for the ones Roblox wouldn't give us; it reads its own assets directly
                ev = threading.Event()
                s["resume"] = ev
                with self.lock:
                    s["need"] = missing
                self.set_status(sid, "need", "asking Studio for %d asset(s)" % sum(len(v) for v in missing.values()))
                ev.wait(300)
                with self.lock:
                    s.pop("need", None)
                more, still = prepare_downloads(s["dir"], self.creds(), prog, self.cache_dir, download=False)
                got = {(f, k) for f, v in missing.items() for k in v if k not in still.get(f, [])}
                got_ids = {k[1:] for _, k in got}
                notes = [n for n in notes if not (("FAILED" in n or "thumbnail" in n) and n.split(" ")[1].rstrip(":") in got_ids)]
                notes += ["%s %s: read in Studio" % ("mesh" if f == "meshes" else "texture", k[1:]) for f, k in sorted(got)]
            s.setdefault("prep_notes", []).extend(notes)
            self.set_status(sid, "queued", "waiting for Blender main thread")
            self.jobs.put((sid, s["dir"]))
        except Exception as e:
            traceback.print_exc()
            self.set_status(sid, "error", "download step failed: %s" % e)


def _write(path, data):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _cached(cache_dir, folder, key):
    """Path of a cached asset (meshes: .rbm, textures: any image extension) or None."""
    exts = (".rbm",) if folder == "meshes" else (".png", ".jpg", ".jpeg", ".tga", ".bmp")
    for ext in exts:
        p = os.path.join(cache_dir, folder, key + ext)
        if os.path.exists(p):
            return p
    return None


def _place(src, dst):
    """Put a cache file into a session without a second copy on disk (hard link; copy if that fails)."""
    if os.path.exists(dst):
        return
    try:
        os.link(src, dst)
    except OSError:
        import shutil
        shutil.copyfile(src, dst)


def _store(src, cache_dir, folder):
    """Remember a session file in the cache (hard link)."""
    os.makedirs(os.path.join(cache_dir, folder), exist_ok=True)
    _place(src, os.path.join(cache_dir, folder, os.path.basename(src)))


def prepare_downloads(session_dir, creds, progress=lambda m: None, cache_dir=None, download=True):
    """Fill the session with every mesh/texture: from Studio's upload, the per-machine cache, or a
    download (parallel, with retries). Returns (notes, missing {"meshes": [key], "textures": [key]})."""
    with open(os.path.join(session_dir, "scene.json"), "rb") as f:
        scene = json.loads(f.read().decode("utf-8"))
    cache_dir = cache_dir or os.path.join(os.path.dirname(session_dir.rstrip("/\\")), "_cache")
    cookie, api_key = creds
    notes, thumbs, todo = [], [], []
    hits = 0
    tp = os.path.join(session_dir, "textures", "thumbnails.json")
    old_thumbs = set(json.load(open(tp))) if os.path.exists(tp) else set()
    for kind, table, folder in (("mesh", scene.get("meshes") or {}, "meshes"), ("texture", scene.get("textures") or {}, "textures")):
        for key, info in table.items():
            key = safe_key(key)
            have = os.path.join(session_dir, folder, key + ".rbm") if kind == "mesh" else _find_texture(session_dir, key)
            if have and os.path.exists(have):
                if re.match(r"^a\d+$", key) and key not in old_thumbs and not _cached(cache_dir, folder, key):
                    _store(have, cache_dir, folder)   # Studio sent it: keep it for next time
                continue
            c = _cached(cache_dir, folder, key)
            if c:
                _place(c, os.path.join(session_dir, folder, os.path.basename(c)))
                hits += 1
                continue
            m = re.search(r"(\d+)$", info.get("uri", ""))
            if not m:
                notes.append("%s %s: not an asset id, can't download" % (kind, info.get("uri", "")))
                continue
            todo.append((kind, key, folder, m.group(1)))

    def get(job):
        kind, key, folder, asset_id = job
        if kind == "mesh":
            mesh = meshio.parse_roblox_mesh(fetch.fetch_asset(asset_id, cookie, api_key))
            return job, meshio.write_rbm(mesh), ".rbm", "full", mesh.get("version", "?")
        raw, how = fetch.fetch_image(asset_id, cookie, api_key)
        ext = pngio.sniff_ext(raw)
        if not ext:
            raise ValueError("downloaded data is not an image")
        return job, raw, ext, how, None

    missing = {"meshes": [], "textures": []}
    if todo and download:
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(get, j): j for j in todo}
            for fut in concurrent.futures.as_completed(futs):
                kind, key, folder, asset_id = futs[fut]
                done += 1
                progress("downloading assets %d/%d" % (done, len(todo)))
                try:
                    _, data, ext, how, ver = fut.result()
                except Exception as e:
                    notes.append("%s %s: download FAILED (%s)" % (kind, asset_id, e))
                    missing[folder].append(key)
                    continue
                dst = os.path.join(session_dir, folder, key + ext)
                _write(dst, data)
                if how == "thumbnail":
                    thumbs.append(key)   # not cached: a later export may get the full image
                    missing[folder].append(key)
                    notes.append("texture %s: used Roblox's public 700px thumbnail (full resolution needs an API key)" % asset_id)
                else:
                    _store(dst, cache_dir, folder)
                    notes.append("%s %s: downloaded%s" % (kind, asset_id, " (.mesh v%s)" % ver if ver else ""))
    elif todo:
        for kind, key, folder, asset_id in todo:
            missing[folder].append(key)
    if hits:
        notes.append("%d asset(s) from the cache (%s)" % (hits, cache_dir))
    try:
        notes.extend(builtin_mats.prepare(session_dir, scene, lambda aid: fetch.fetch_image(aid, cookie, api_key), progress))
    except Exception as e:
        notes.append("built-in materials: FAILED (%s)" % e)
    if thumbs:
        tp = os.path.join(session_dir, "textures", "thumbnails.json")
        old = []
        if os.path.exists(tp):
            with open(tp) as f:
                old = json.load(f)
        with open(tp, "w") as f:
            json.dump(sorted(set(old + thumbs)), f)
    return notes, {k: v for k, v in missing.items() if v}


def _find_texture(session_dir, key):
    for ext in (".png", ".jpg", ".jpeg", ".tga", ".bmp"):
        p = os.path.join(session_dir, "textures", key + ext)
        if os.path.exists(p):
            return p
    return None
