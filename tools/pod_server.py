#!/usr/bin/env python3
"""Local server for pod.html, the click wheel audio player.

Serves this repo plus the audio folder. Audio is sent in byte ranges so the
browser can seek (Safari won't play audio at all without them), and the track
list the page reads at /audio/index.json is built straight from the folders,
so new folders or files just need a page reload.

  python3 tools/pod_server.py          then open http://localhost:8000/pod.html
  python3 tools/pod_server.py --lan    also reachable from a phone on the same Wi-Fi
  python3 tools/pod_server.py --audio ~/Audiobooks
  python3 tools/pod_server.py --build  no server: writes audio/index.js so pod.html
                                       works opened straight from disk (rerun it
                                       after adding files)

Every folder inside the audio folder is its own warehouse, tracks in file name
order. Titles, artist and cover art come from the files' tags when they have
them, otherwise from the file names. To rename a warehouse or set its artist,
put an info.json like {"title": "...", "artist": "..."} in its folder; a
cover.jpg or cover.png there replaces the cover art.
"""

import argparse
import http.server
import json
import os
import re
import socket
import struct
import sys
import threading
import urllib.parse
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".weba"}
TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".weba": "audio/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png")
TEXT_FRAMES = {"TIT2": "title", "TT2": "title", "TALB": "album", "TAL": "album",
               "TPE1": "artist", "TP1": "artist", "TPE2": "band", "TP2": "band"}

# Layer III bitrates (kbps) for MPEG-1 and MPEG-2/2.5, and sample rates by version bits.
BITRATES = {
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def natural_key(name):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def syncsafe(b):
    return (b[0] & 0x7F) << 21 | (b[1] & 0x7F) << 14 | (b[2] & 0x7F) << 7 | (b[3] & 0x7F)


def decode_text(body):
    if not body:
        return ""
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(body[0], "latin-1")
    return body[1:].decode(codec, errors="replace").split("\x00")[0].strip()


def picture_location(head, frame_id, body_at, size):
    """Where the image bytes of an APIC/PIC frame start, from its first bytes."""
    enc = head[0]
    if frame_id == "PIC":
        mime = "image/png" if head[1:4].upper() == b"PNG" else "image/jpeg"
        i = 5
    else:
        j = head.index(b"\x00", 1)
        mime = head[1:j].decode("latin-1").lower() or "image/jpeg"
        if "/" not in mime:
            mime = "image/" + mime.replace("jpg", "jpeg")
        i = j + 2
    if enc in (1, 2):  # UTF-16 description ends with a double null
        while head[i:i + 2] != b"\x00\x00":
            i += 2
        i += 2
    else:
        i = head.index(b"\x00", i) + 1
    return body_at + i, size - i, mime


def read_id3(f):
    """Returns (tags, picture, audio_start). Skips over frame bodies it doesn't
    need, so big embedded covers aren't read."""
    f.seek(0)
    head = f.read(10)
    tags, picture = {}, None
    if len(head) < 10 or head[:3] != b"ID3":
        return tags, picture, 0
    ver, flags, size = head[3], head[5], syncsafe(head[6:10])
    end = 10 + size
    pos = 10
    if flags & 0x40 and ver >= 3:
        ext = f.read(4)
        pos += syncsafe(ext) if ver == 4 else struct.unpack(">I", ext)[0] + 4
    id_len, hdr_len = (3, 6) if ver == 2 else (4, 10)
    while pos + hdr_len <= end:
        f.seek(pos)
        fh = f.read(hdr_len)
        frame_id = fh[:id_len]
        if not re.fullmatch(rb"[A-Z0-9]+", frame_id):
            break
        if ver == 2:
            frame_size = int.from_bytes(fh[3:6], "big")
        elif ver == 4:
            frame_size = syncsafe(fh[4:8])
        else:
            frame_size = struct.unpack(">I", fh[4:8])[0]
        frame_id = frame_id.decode()
        body_at = pos + hdr_len
        if frame_id in TEXT_FRAMES and TEXT_FRAMES[frame_id] not in tags:
            text = decode_text(f.read(min(frame_size, 4096)))
            if text:
                tags[TEXT_FRAMES[frame_id]] = text
        elif frame_id in ("APIC", "PIC") and picture is None:
            try:
                picture = picture_location(f.read(min(frame_size, 1024)), frame_id, body_at, frame_size)
            except ValueError:
                pass
        pos = body_at + frame_size
    return tags, picture, end + (10 if flags & 0x10 else 0)


def read_id3v1(f):
    f.seek(-128, os.SEEK_END)
    tail = f.read(128)
    if tail[:3] != b"TAG":
        return {}, False

    def field(a, b):
        return tail[a:b].split(b"\x00")[0].decode("latin-1").strip()

    tags = {"title": field(3, 33), "artist": field(33, 63), "album": field(63, 93)}
    return {k: v for k, v in tags.items() if v}, True


def frame_header(h):
    """(kbps, sample rate, samples per frame, frame length, side info size) for an
    MPEG Layer III frame header, or None."""
    if len(h) < 4 or h[0] != 0xFF or h[1] & 0xE0 != 0xE0:
        return None
    ver, layer, bri, sri = (h[1] >> 3) & 3, (h[1] >> 1) & 3, h[2] >> 4, (h[2] >> 2) & 3
    if ver == 1 or layer != 1 or bri in (0, 15) or sri == 3:
        return None
    kbps = BITRATES[1 if ver == 3 else 2][bri]
    rate = RATES[ver][sri]
    mono = h[3] >> 6 == 3
    length = (144 if ver == 3 else 72) * kbps * 1000 // rate + ((h[2] >> 1) & 1)
    side = (17 if mono else 32) if ver == 3 else (9 if mono else 17)
    return kbps, rate, 1152 if ver == 3 else 576, length, side


def mp3_duration(f, start, file_size, v1):
    f.seek(start)
    buf = f.read(65536)
    for i in range(len(buf) - 4):
        if buf[i] != 0xFF:
            continue
        info = frame_header(buf[i:i + 4])
        if not info:
            continue
        kbps, rate, spf, length, side = info
        following = buf[i + length:i + length + 4]
        if len(following) == 4 and not frame_header(following):
            continue  # a stray 0xFF, not a real frame
        x = i + 4 + side
        if buf[x:x + 4] in (b"Xing", b"Info") and struct.unpack(">I", buf[x + 4:x + 8])[0] & 1:
            return struct.unpack(">I", buf[x + 8:x + 12])[0] * spf / rate
        if buf[i + 36:i + 40] == b"VBRI":
            return struct.unpack(">I", buf[i + 50:i + 54])[0] * spf / rate
        return (file_size - start - i - (128 if v1 else 0)) * 8 / (kbps * 1000)
    return None


_cache = {}
_cache_lock = threading.Lock()


def track_meta(path):
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    meta = {"tags": {}, "picture": None, "duration": None}
    try:
        with open(path, "rb") as f:
            tags, picture, start = read_id3(f)
            v1_tags, v1 = read_id3v1(f) if st.st_size > 128 else ({}, False)
            meta["tags"] = {**v1_tags, **tags}
            meta["picture"] = picture
            if path.suffix.lower() == ".mp3":
                meta["duration"] = mp3_duration(f, start, st.st_size, v1)
    except (OSError, ValueError, struct.error, IndexError):
        pass
    with _cache_lock:
        _cache[key] = meta
    return meta


def common_words(titles):
    """Words every title starts with, if there are at least two and every title
    has something after them."""
    if len(titles) < 2:
        return ""
    split = [t.split() for t in titles]
    n = 0
    while all(len(s) > n + 1 for s in split) and len({s[n].lower() for s in split}) == 1:
        n += 1
    return " ".join(split[0][:n]) if n >= 2 else ""


def tidy_title(title, prefixes):
    for prefix in prefixes:
        if prefix and title.lower().startswith(prefix.lower() + " "):
            title = title[len(prefix):].lstrip(" -:_")
            break
    title = title.replace("_", " ")
    return re.sub(r"(\w)- ", r"\1: ", title).strip()  # "Interlude- Chandra" was "Interlude: Chandra"


def audio_files(folder):
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith(".")]
    return sorted(files, key=lambda p: natural_key(p.name))


def cover_file(folder):
    for name in COVER_NAMES:
        if (folder / name).is_file():
            return folder / name
    return None


def build_index(audio_dir):
    warehouses = []
    folders = [p for p in audio_dir.iterdir() if p.is_dir() and not p.name.startswith(".")] if audio_dir.is_dir() else []
    for folder in sorted(folders, key=lambda p: natural_key(p.name)):
        files = audio_files(folder)
        if not files:
            continue
        metas = [track_meta(p) for p in files]
        raw = [m["tags"].get("title") or re.sub(r"^\d+[\s._-]*", "", p.stem) or p.stem
               for p, m in zip(files, metas)]
        albums = Counter(m["tags"]["album"] for m in metas if m["tags"].get("album"))
        artists = Counter(m["tags"].get("artist") or m["tags"].get("band") for m in metas)
        artists.pop(None, None)
        album = albums.most_common(1)[0][0] if albums else ""
        prefix = common_words(raw)
        info = {}
        try:
            info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        has_cover = bool(cover_file(folder)) or any(m["picture"] for m in metas)
        warehouses.append({
            "id": folder.name,
            "title": info.get("title") or album or prefix or folder.name,
            "artist": info.get("artist") or (artists.most_common(1)[0][0] if artists else ""),
            "cover": "audio/%s/cover" % urllib.parse.quote(folder.name) if has_cover else None,
            "tracks": [
                {"file": p.name, "title": tidy_title(t, (album, prefix)) or p.stem,
                 "duration": round(m["duration"], 1) if m["duration"] else None}
                for p, m, t in zip(files, metas, raw)
            ],
        })
    return {"warehouses": warehouses}


def embedded_cover(folder):
    """(image bytes, mime type) of the first cover embedded in the folder's tracks."""
    for p in audio_files(folder):
        picture = track_meta(p)["picture"]
        if not picture:
            continue
        offset, length, mime = picture
        with open(p, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        for magic, kind in ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png")):
            at = data.find(magic, 0, 64)
            if at >= 0:
                return data[at:], kind
        return data, mime
    return None


def write_static(audio_dir):
    """Writes index.js into the audio folder, plus cover files pulled out of the
    tags, so pod.html works from file:// where it can't fetch anything."""
    index = build_index(audio_dir)
    for w in index["warehouses"]:
        folder = audio_dir / w["id"]
        file = cover_file(folder)
        if not file:
            embedded = embedded_cover(folder)
            if embedded:
                file = folder / ("cover.png" if embedded[1] == "image/png" else "cover.jpg")
                file.write_bytes(embedded[0])
        w["cover"] = "audio/%s/%s" % (urllib.parse.quote(w["id"]), urllib.parse.quote(file.name)) if file else None
    out = audio_dir / "index.js"
    out.write_text("window.POD_INDEX = %s;\n" % json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return out, index


def safe_join(base, rel):
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    return base.joinpath(*parts)


class Handler(http.server.SimpleHTTPRequestHandler):
    audio_dir = ROOT / "audio"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.route(send_body=True)

    def do_HEAD(self):
        self.route(send_body=False)

    def route(self, send_body):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        try:
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/pod.html")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path == "/audio/index.json":
                data = json.dumps(build_index(self.audio_dir), ensure_ascii=False).encode()
                self.send_bytes(data, "application/json; charset=utf-8", send_body)
            elif re.fullmatch(r"/audio/[^/]+/cover", path):
                self.send_cover(safe_join(self.audio_dir, path.split("/")[2]), send_body)
            elif path.startswith("/audio/"):
                self.send_file(safe_join(self.audio_dir, path[len("/audio/"):]), send_body)
            elif send_body:
                super().do_GET()
            else:
                super().do_HEAD()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser often drops audio requests when seeking

    def send_bytes(self, data, ctype, send_body, cache=False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def send_cover(self, folder, send_body):
        if not folder.is_dir():
            return self.send_error(404)
        file = cover_file(folder)
        if file:
            return self.send_file(file, send_body)
        embedded = embedded_cover(folder)
        if embedded:
            return self.send_bytes(embedded[0], embedded[1], send_body, cache=True)
        self.send_error(404)

    def send_file(self, file, send_body):
        if not file.is_file():
            return self.send_error(404)
        size = file.stat().st_size
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        partial = bool(m and (m.group(1) or m.group(2)))
        if partial:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", TYPES.get(file.suffix.lower(), "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Last-Modified", self.date_time_string(int(file.stat().st_mtime)))
        self.end_headers()
        if not send_body:
            return
        with open(file, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # picks the outgoing interface, sends nothing
        return s.getsockname()[0]
    except OSError:
        return "your-computer's-ip"
    finally:
        s.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    p.add_argument("--lan", action="store_true", help="also listen on your network, for a phone on the same Wi-Fi")
    p.add_argument("--audio", default=str(ROOT / "audio"), help="folder of warehouses (default: audio/ in this repo)")
    p.add_argument("--build", action="store_true", help="write audio/index.js for opening pod.html from disk, then exit")
    args = p.parse_args()
    Handler.audio_dir = Path(os.path.expanduser(args.audio)).resolve()
    if args.build:
        out, index = write_static(Handler.audio_dir)
        tracks = sum(len(w["tracks"]) for w in index["warehouses"])
        print(f"Wrote {out} ({len(index['warehouses'])} warehouses, {tracks} tracks).")
        print(f"Now open {ROOT / 'pod.html'} in your browser. Run this again after adding audio.")
        return
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), Handler)
    except OSError as e:
        sys.exit(f"Can't use port {args.port} ({e.strerror}). Try --port {args.port + 1}.")
    server.daemon_threads = True
    index = build_index(Handler.audio_dir)  # also reads every file's tags up front
    tracks = sum(len(w["tracks"]) for w in index["warehouses"])
    print(f"{len(index['warehouses'])} warehouses, {tracks} tracks in {Handler.audio_dir}")
    print(f"Open http://localhost:{args.port}/pod.html")
    if args.lan:
        print(f"On your phone: http://{lan_ip()}:{args.port}/pod.html")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
