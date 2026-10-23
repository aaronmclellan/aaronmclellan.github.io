#!/usr/bin/env python3
"""Download every audio file from a paginated website.

Point it at the first page of a listing. It downloads every audio file the
page links to or embeds, finds the link to the next page, and keeps going
until it runs out of pages.

  python3 scrape_audio.py https://example.com/episodes
  python3 scrape_audio.py https://example.com/episodes -o ~/Music/show
  python3 scrape_audio.py https://example.com/episodes --dry-run > urls.txt

Finding the next page:
  It looks for rel="next" links, then links labelled Next, Older, », ›,
  then numbered page links (2, 3, 4...). If it picks the wrong link, tell it
  which one with --next-text "Older posts", or put {n} in the URL where the
  page number goes and it counts up until a page has nothing new:

  python3 scrape_audio.py "https://example.com/archive?page={n}"

Audio on separate pages:
  If each item has its own page and the audio is only there, pass --follow
  with a piece of those pages' URLs and it opens each one:

  python3 scrape_audio.py https://example.com/blog --follow "/episode/"

Re-running is safe: anything already downloaded is skipped, so it also works
as "fetch whatever is new since last time". Standard library only.
"""

import argparse
import gzip
import http.client
import http.cookiejar
import mimetypes
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.parser import HTMLParser

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
MANIFEST = ".scrape_audio.tsv"  # url<TAB>filename for everything already saved
RETRY_CODES = {408, 425, 429, 500, 502, 503, 504}
TRIES = 4

AUDIO_EXTS = "mp3|m4a|m4b|aac|wav|flac|ogg|oga|opus|wma|aif|aiff|alac|amr|mka|weba"
AUDIO_PATH_RE = re.compile(r"\.(?:%s)$" % AUDIO_EXTS, re.I)
# Audio URLs written into scripts and JSON blobs, quoted and possibly relative.
QUOTED_AUDIO_RE = re.compile(
    r"""["']([^"'\s<>]+?\.(?:%s)(?:\?[^"'\s<>]*)?)["']""" % AUDIO_EXTS, re.I
)
# Full audio URLs anywhere in the text of the page.
BARE_AUDIO_RE = re.compile(
    r"""https?://[^\s"'<>()\\]+?\.(?:%s)(?:\?[^\s"'<>()\\]*)?(?=[\s"'<>()\\,;]|$)"""
    % AUDIO_EXTS,
    re.I,
)
NEXT_TEXT_RE = re.compile(
    r"^(?:(?:next|older)(?: page| posts| entries| episodes)?\s*[›»>→]?|[›»>→])$", re.I
)
NEXT_CLASS_RE = re.compile(r"(?:^|[\s_-])next(?:$|[\s_-])", re.I)
PAGE_NUM_RE = re.compile(r"(?:[?&](?:page|p|pg|paged)=|/page/)(\d+)", re.I)

CONTENT_TYPE_EXT = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/webm": ".weba",
    "audio/x-ms-wma": ".wma",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
}

PRINT_LOCK = threading.Lock()


def log(message):
    with PRINT_LOCK:
        print(message, file=sys.stderr, flush=True)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def describe(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        reason = str(e.reason)
        if "CERTIFICATE_VERIFY_FAILED" in reason:
            reason += (
                "; if Python came from python.org, run 'Install Certificates.command'"
                " in its Applications folder"
            )
        return reason
    return str(e) or type(e).__name__


def clean_url(url):
    url = urllib.parse.urldefrag(url.strip())[0]
    return urllib.parse.quote(url, safe="%/:=&?~#+!$,;'@()*[]")


def url_key(url):
    return url.rstrip("/")


def same_site(a, b):
    def host(u):
        return re.sub(r"^www\.", "", urllib.parse.urlsplit(u).hostname or "")

    return host(a) == host(b)


def page_number(url):
    m = PAGE_NUM_RE.search(url)
    return int(m.group(1)) if m else None


def is_audio_url(value, allow_spaces=False):
    value = value.strip()
    if not value or len(value) > 2000 or value.startswith(("data:", "javascript:")):
        return False
    if not allow_spaces and re.search(r"\s", value):
        return False
    parts = urllib.parse.urlsplit(value)
    if AUDIO_PATH_RE.search(urllib.parse.unquote(parts.path)):
        return True
    return any(AUDIO_PATH_RE.search(v) for _, v in urllib.parse.parse_qsl(parts.query))


class PageParser(HTMLParser):
    """Collects audio URLs, links and next-page candidates from one page."""

    def __init__(self, next_text=None):
        super().__init__(convert_charrefs=True)
        self.next_text = next_text.lower() if next_text else None
        self.base = None
        self.audio = []
        self.links = []  # (href, text) for every <a>
        self.next_candidates = []  # (score, href)
        self.script_text = []
        self.body_text = []
        self._a = None
        self._audio_depth = 0
        self._in_script = False
        self._next_li = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        for name, value in a.items():
            if name in ("href", "src") or name.startswith("data-") or name in ("content", "value"):
                if is_audio_url(value, allow_spaces=name in ("href", "src")):
                    self.audio.append(value.strip())
                elif value.lstrip().startswith(("{", "[")):
                    self.script_text.append(value + "\n")  # JSON in a data attribute

        if tag == "img" and self._a is not None:
            self._a["text"].append(" " + a.get("alt", "") + " ")
        if tag == "a" and self._a is not None:
            self._finish_link()

        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "audio":
            self._audio_depth += 1
            if a.get("src"):
                self.audio.append(a["src"].strip())
        elif tag == "source" and a.get("src"):
            if self._audio_depth or a.get("type", "").lower().startswith("audio/"):
                self.audio.append(a["src"].strip())
        elif tag == "meta" and a.get("content"):
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in ("og:audio", "og:audio:url", "og:audio:secure_url"):
                self.audio.append(a["content"].strip())
        elif tag == "link" and a.get("href"):
            if "next" in rel:
                self.next_candidates.append((100, a["href"]))
            elif a.get("type", "").lower().startswith("audio/"):
                self.audio.append(a["href"].strip())
        elif tag == "a" and a.get("href"):
            if a.get("type", "").lower().startswith("audio/") or is_audio_url(a.get("download", "")):
                self.audio.append(a["href"].strip())
            self._a = {"href": a["href"], "attrs": a, "rel": rel, "text": [], "next_li": self._next_li}
        elif tag == "li":
            marker = a.get("class", "") + " " + a.get("id", "")
            self._next_li = bool(NEXT_CLASS_RE.search(marker))
        elif tag in ("script", "style"):
            self._in_script = tag == "script"

    def handle_endtag(self, tag):
        if tag == "audio" and self._audio_depth:
            self._audio_depth -= 1
        elif tag == "script":
            self._in_script = False
            self.script_text.append("\n")
        elif tag == "li":
            self._next_li = False
        elif tag == "a" and self._a is not None:
            self._finish_link()

    def handle_data(self, data):
        if self._a is not None:
            self._a["text"].append(data)
        (self.script_text if self._in_script else self.body_text).append(data)

    def _finish_link(self):
        link, self._a = self._a, None
        href = link["href"].strip()
        text = " ".join("".join(link["text"]).split())
        self.links.append((href, text))
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return
        attrs = link["attrs"]
        label = " ".join((attrs.get("aria-label", "") + " " + attrs.get("title", "")).split())
        marker = attrs.get("class", "") + " " + attrs.get("id", "")
        if attrs.get("aria-disabled") == "true" or "disabled" in marker.lower():
            return
        if self.next_text and (self.next_text in text.lower() or self.next_text in label.lower()):
            score = 200
        elif "next" in link["rel"]:
            score = 100
        elif label and NEXT_TEXT_RE.match(label):
            score = 80
        elif text and NEXT_TEXT_RE.match(text):
            score = 60
        elif NEXT_CLASS_RE.search(marker) or link["next_li"]:
            score = 50
        else:
            return
        self.next_candidates.append((score, href))

    def finish(self):
        if self._a is not None:
            self._finish_link()


@dataclass
class Page:
    url: str
    audio: list  # absolute audio URLs, in page order
    links: list  # (absolute url, link text)
    next_candidates: list  # (score, absolute url)


def parse_page(html, url, next_text=None):
    parser = PageParser(next_text)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass  # keep whatever it managed to read
    parser.finish()
    base = urllib.parse.urljoin(url, parser.base) if parser.base else url

    def resolve(href):
        return clean_url(urllib.parse.urljoin(base, href.strip()))

    def is_http(u):
        return urllib.parse.urlsplit(u).scheme in ("http", "https")

    script = "".join(parser.script_text)
    script = script.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    raw = (
        parser.audio
        + QUOTED_AUDIO_RE.findall(script)
        + BARE_AUDIO_RE.findall(script)
        + BARE_AUDIO_RE.findall(" ".join(parser.body_text))
    )
    audio = []
    for u in map(resolve, raw):
        if is_http(u) and u not in audio:
            audio.append(u)
    links = [(resolve(h), t) for h, t in parser.links if is_http(resolve(h))]
    nexts = [(s, resolve(h)) for s, h in parser.next_candidates if is_http(resolve(h))]
    return Page(url, audio, links, nexts)


def pick_next(page, n, visited):
    """Best next-page URL that hasn't been visited, or None."""
    candidates = list(page.next_candidates)
    want = str(n + 1)
    candidates += [(30, u) for u, text in page.links if text.lower() in (want, "page " + want)]
    candidates.sort(key=lambda c: -c[0])  # stable, so page order breaks ties
    for score, u in candidates:
        if url_key(u) in visited or url_key(u) == url_key(page.url):
            continue
        if score < 200 and not same_site(u, page.url):
            continue
        return u
    return None


def retry_after(e):
    value = e.headers.get("Retry-After", "") if e.headers else ""
    return min(int(value), 60) if value.isdigit() else None


def is_fatal(e):
    reason = getattr(e, "reason", e)
    return isinstance(reason, (ssl.SSLCertVerificationError, ssl.SSLError))


class Fetcher:
    def __init__(self, headers, timeout):
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        self.headers.update(headers)
        self.timeout = timeout

    def open(self, url, referer=None, accept="*/*"):
        headers = {"Accept": accept}
        if referer:
            headers["Referer"] = referer
        headers.update(self.headers)
        return self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout)

    def page(self, url, referer=None):
        """Returns (final url after redirects, html text)."""
        for attempt in range(1, TRIES + 1):
            try:
                with self.open(url, referer, "text/html,application/xhtml+xml,*/*;q=0.8") as resp:
                    body = resp.read()
                    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                    charset = resp.headers.get_content_charset() or sniff_charset(body)
                    try:
                        return resp.geturl(), body.decode(charset or "utf-8", errors="replace")
                    except LookupError:
                        return resp.geturl(), body.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code not in RETRY_CODES or attempt == TRIES:
                    raise
                time.sleep(retry_after(e) or 2 ** attempt)
            except (OSError, http.client.HTTPException) as e:
                if is_fatal(e) or attempt == TRIES:
                    raise
                time.sleep(2 ** attempt)


def sniff_charset(body):
    m = re.search(rb"""<meta[^>]+charset=["']?([\w-]+)""", body[:4096], re.I)
    return m.group(1).decode("ascii") if m else None


def sanitize(name):
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .")
    stem, ext = os.path.splitext(name)
    if len(name) > 180:
        name = stem[: 180 - len(ext)] + ext
    return name or "audio"


def filename_for(url, resp):
    name = resp.headers.get_filename()
    if name:
        try:
            name = name.encode("latin-1").decode("utf-8")  # headers arrive as latin-1
        except UnicodeError:
            pass
    else:
        for candidate in (url, resp.geturl()):
            parts = urllib.parse.urlsplit(candidate)
            base = os.path.basename(urllib.parse.unquote(parts.path))
            if AUDIO_PATH_RE.search(base):
                name = base
                break
            in_query = [v for _, v in urllib.parse.parse_qsl(parts.query) if AUDIO_PATH_RE.search(v)]
            if in_query:
                name = os.path.basename(in_query[0])
                break
    if not name:
        name = os.path.basename(urllib.parse.unquote(urllib.parse.urlsplit(url).path))
    name = sanitize(name)
    if not AUDIO_PATH_RE.search(name):
        ctype = resp.headers.get_content_type()
        name += CONTENT_TYPE_EXT.get(ctype) or mimetypes.guess_extension(ctype) or ""
    return name


class NotAudio(Exception):
    pass


class ShortRead(OSError):
    pass


class Downloader:
    def __init__(self, fetcher, out_dir, workers):
        self.fetcher = fetcher
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.manifest = os.path.join(out_dir, MANIFEST)
        self.done = {}
        if os.path.exists(self.manifest):
            with open(self.manifest, encoding="utf-8") as f:
                for line in f:
                    url, _, name = line.rstrip("\n").partition("\t")
                    if name:
                        self.done[url] = name
        self.claimed = set()
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.saved = self.had = self.bytes = 0
        self.failed = []

    def submit(self, url, referer):
        self.pool.submit(self._run, url, referer)

    def wait(self):
        self.pool.shutdown(wait=True)

    def _run(self, url, referer):
        try:
            self._download(url, referer)
        except Exception as e:
            with self.lock:
                self.failed.append((url, describe(e)))
            log(f"  failed  {url}  ({describe(e)})")

    def _download(self, url, referer):
        name = self.done.get(url)
        if name and os.path.exists(os.path.join(self.out_dir, name)):
            with self.lock:
                self.had += 1
            log(f"  have    {name}")
            return
        path = None
        for attempt in range(1, TRIES + 1):
            try:
                with self.fetcher.open(url, referer) as resp:
                    ctype = resp.headers.get_content_type()
                    if ctype in ("text/html", "application/xhtml+xml"):
                        raise NotAudio("got a web page instead of audio")
                    length = int(resp.headers.get("Content-Length") or 0)
                    if path is None:
                        path, existing = self._claim(filename_for(url, resp), length)
                        if existing:
                            self._record(url, path, length, existing=True)
                            return
                    got = self._stream(resp, path + ".part")
                if length and got < length:
                    raise ShortRead(f"connection dropped after {human_size(got)} of {human_size(length)}")
                os.replace(path + ".part", path)
                self._record(url, path, got)
                return
            except urllib.error.HTTPError as e:
                if e.code not in RETRY_CODES or attempt == TRIES:
                    raise
                time.sleep(retry_after(e) or 2 ** attempt)
            except (OSError, http.client.HTTPException) as e:
                if is_fatal(e) or attempt == TRIES:
                    raise
                time.sleep(2 ** attempt)

    def _claim(self, name, length):
        """Picks a free filename. If a file of that name and the same size is
        already there, treats it as downloaded."""
        stem, ext = os.path.splitext(name)
        candidate, i = name, 2
        with self.lock:
            while True:
                path = os.path.join(self.out_dir, candidate)
                if candidate.lower() not in self.claimed:
                    if not os.path.exists(path):
                        break
                    if length and os.path.getsize(path) == length:
                        self.claimed.add(candidate.lower())
                        return path, True
                candidate = f"{stem} ({i}){ext}"
                i += 1
            self.claimed.add(candidate.lower())
            return path, False

    def _stream(self, resp, tmp):
        got = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    return got
                f.write(chunk)
                got += len(chunk)

    def _record(self, url, path, size, existing=False):
        name = os.path.basename(path)
        with self.lock:
            self.done[url] = name
            with open(self.manifest, "a", encoding="utf-8") as f:
                f.write(f"{url}\t{name}\n")
            if existing:
                self.had += 1
            else:
                self.saved += 1
                self.bytes += size
        log(f"  {'have' if existing else 'saved'}   {name}  ({human_size(size)})")


def crawl(args):
    headers = {}
    for h in args.header:
        k, sep, v = h.partition(":")
        if not sep:
            sys.exit(f"Bad header {h!r}, expected 'Name: value'")
        headers[k.strip()] = v.strip()
    fetcher = Fetcher(headers, args.timeout)
    out_dir = os.path.expanduser(args.out)
    downloader = None if args.dry_run else Downloader(fetcher, out_dir, args.workers)
    match = re.compile(args.match, re.I) if args.match else None
    follow = re.compile(args.follow, re.I) if args.follow else None
    templated = "{n}" in args.url

    seen_audio, visited, followed = set(), set(), set()

    def take(audio, referer):
        fresh = [u for u in audio if u not in seen_audio and (not match or match.search(u))]
        seen_audio.update(fresh)
        return fresh

    def send(urls, referer):
        for u in urls:
            if downloader:
                downloader.submit(u, referer)
            else:
                print(u, flush=True)

    n = args.start if templated else (page_number(args.url) or 1)
    url = clean_url(args.url.replace("{n}", str(n)))
    pages = 0
    while url:
        if args.max_pages and pages >= args.max_pages:
            log(f"Stopped after {pages} pages (--max-pages).")
            break
        if pages:
            time.sleep(args.delay)
        visited.add(url_key(url))
        try:
            final, html = fetcher.page(url)
        except Exception as e:
            if templated and pages and isinstance(e, urllib.error.HTTPError) and e.code == 404:
                log(f"{url} doesn't exist, so that was the last page.")
            else:
                log(f"Couldn't load {url} ({describe(e)}), stopping.")
            break
        pages += 1
        visited.add(url_key(final))
        page = parse_page(html, final, args.next_text)
        fresh = take(page.audio, final)
        log(f"Page {pages}: {final}  ({len(page.audio)} audio, {len(fresh)} new)")
        send(fresh, final)
        new = len(fresh)

        if follow:
            on_page = set(page.audio)
            for link, _ in page.links:
                if not follow.search(link) or link in on_page or url_key(link) in followed:
                    continue
                followed.add(url_key(link))
                time.sleep(args.delay)
                try:
                    item_url, item_html = fetcher.page(link, referer=final)
                except Exception as e:
                    log(f"  couldn't open {link} ({describe(e)})")
                    continue
                item = parse_page(item_html, item_url)
                fresh = take(item.audio, item_url)
                log(f"  {item_url}  ({len(item.audio)} audio, {len(fresh)} new)")
                send(fresh, item_url)
                new += len(fresh)

        if templated:
            n += 1
            if not new:
                log("Nothing new on that page, so that was the last page.")
                url = None
            elif args.end and n > args.end:
                url = None
            else:
                url = clean_url(args.url.replace("{n}", str(n)))
        else:
            url = pick_next(page, n, visited)
            if url:
                n = page_number(url) or n + 1
            else:
                log("No link to a next page, so that was the last page.")

    found = len(seen_audio)
    if not found and pages:
        log(
            "No audio found. If the site builds its list with JavaScript this script"
            " can't see it; if each item has its own page, try --follow."
        )
    if not downloader:
        log(f"\nDone. {pages} pages, {found} audio files found (dry run, nothing downloaded).")
        return 0
    downloader.wait()
    summary = [f"{downloader.saved} downloaded ({human_size(downloader.bytes)})"]
    if downloader.had:
        summary.append(f"{downloader.had} already had")
    if downloader.failed:
        summary.append(f"{len(downloader.failed)} failed")
    log(f"\nDone. {pages} pages, {found} audio files: {', '.join(summary)}. Saved in {out_dir}")
    if downloader.failed:
        log("Failed (run again to retry):")
        for u, why in downloader.failed:
            log(f"  {u}  ({why})")
        return 1
    return 0


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("url", help="first page of the listing; put {n} where the page number goes to count pages instead")
    p.add_argument("-o", "--out", default="audio", help="folder to save into (default: ./audio)")
    p.add_argument("--next-text", metavar="TEXT", help='text of the next page link, e.g. "Older posts"')
    p.add_argument("--follow", metavar="PATTERN", help="also open links whose URL matches this regex and grab their audio")
    p.add_argument("--match", metavar="PATTERN", help="only download audio URLs matching this regex")
    p.add_argument("--start", type=int, default=1, help="first {n} page number (default: 1)")
    p.add_argument("--end", type=int, help="last {n} page number")
    p.add_argument("--max-pages", type=int, metavar="N", help="stop after N listing pages")
    p.add_argument("--delay", type=float, default=1.0, metavar="SECONDS", help="pause between pages (default: 1)")
    p.add_argument("--workers", type=int, default=3, help="downloads at once (default: 3)")
    p.add_argument("-H", "--header", action="append", default=[], metavar="'Name: value'",
                   help="extra request header, e.g. -H 'Cookie: session=abc' for sites you're logged into")
    p.add_argument("--timeout", type=float, default=30, metavar="SECONDS", help="network timeout (default: 30)")
    p.add_argument("--dry-run", action="store_true", help="list the audio URLs (to stdout) without downloading")
    args = p.parse_args()
    if urllib.parse.urlsplit(args.url).scheme not in ("http", "https"):
        p.error("url must start with http:// or https://")
    try:
        sys.exit(crawl(args))
    except KeyboardInterrupt:
        log("\nStopped. Run the same command again to pick up where it left off.")
        os._exit(130)


if __name__ == "__main__":
    main()
