"""Homepage SEO files that ship in frontend/public (no live HTTP)."""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "frontend" / "public"
INDEX = PUBLIC / "index.html"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip = True

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html():
    return INDEX.read_text(encoding="utf-8")


def test_title_and_meta_description():
    html = _html()
    assert "<title>Sleeping Stock NMTS | Non-Moving Tracking System for Spare Parts</title>" in html
    assert (
        'content="Sleeping Stock NMTS helps automobile dealers and branches identify, '
        'match and move non-moving spare parts across their network, reducing dead stock '
        'and unlocking working capital."'
    ) in html
    assert 'rel="canonical" href="https://sleepingstock.in/"' in html


def test_favicon_links_and_files_exist():
    html = _html()
    assert 'href="/favicon.ico"' in html
    assert 'href="/favicon-48x48.png"' in html
    assert 'href="/android-chrome-192x192.png"' in html
    ico = (PUBLIC / "favicon.ico").read_bytes()
    png48 = (PUBLIC / "favicon-48x48.png").read_bytes()
    png192 = (PUBLIC / "android-chrome-192x192.png").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00"
    assert png48[:8] == b"\x89PNG\r\n\x1a\n"
    assert png192[:8] == b"\x89PNG\r\n\x1a\n"
    assert png48[16:24] == b"\x00\x00\x00\x30\x00\x00\x00\x30"  # 48x48
    assert png192[16:24] == b"\x00\x00\x00\xc0\x00\x00\x00\xc0"  # 192x192


def test_open_graph_and_structured_data():
    html = _html()
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert 'property="og:image" content="https://sleepingstock.in/og-image.png"' in html
    assert 'property="og:url" content="https://sleepingstock.in/"' in html
    match = re.search(
        r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
        html,
        re.S,
    )
    assert match, "JSON-LD script missing"
    data = json.loads(match.group(1))
    types = {node.get("@type") for node in data["@graph"]}
    assert types == {"Organization", "WebSite"}
    org = next(n for n in data["@graph"] if n["@type"] == "Organization")
    assert org["name"] == "Sleeping Stock"
    assert org["logo"]["url"] == "https://sleepingstock.in/android-chrome-192x192.png"
    site = next(n for n in data["@graph"] if n["@type"] == "WebSite")
    assert site["url"] == "https://sleepingstock.in/"
    assert "Non-Moving Tracking System designed for automobile dealer networks" in site["description"]


def test_crawlable_homepage_text_is_visible_not_hidden():
    html = _html()
    assert "display:none" not in html.lower()
    assert "visibility:hidden" not in html.lower()
    assert "You need to enable JavaScript to run this app." not in html
    parser = _TextExtractor()
    parser.feed(html)
    text = " ".join(parser.parts)
    assert "Sleeping Stock NMTS is a Non-Moving Tracking System designed for automobile dealer networks." in text
    assert "Sign in to Sleeping Stock" in text
    assert '<div id="root">' in html


def test_robots_and_sitemap_allow_homepage():
    robots = (PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert "Disallow: /" not in robots
    assert "Allow: /" in robots
    assert "Sitemap: https://sleepingstock.in/sitemap.xml" in robots
    sitemap = (PUBLIC / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://sleepingstock.in/</loc>" in sitemap
    assert "/login" not in sitemap
    assert "/orders" not in sitemap


def test_og_image_exists():
    og = (PUBLIC / "og-image.png").read_bytes()
    assert og[:8] == b"\x89PNG\r\n\x1a\n"
    assert og[16:24] == b"\x00\x00\x04\xb0\x00\x00\x02\x76"  # 1200x630
