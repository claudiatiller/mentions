# -*- coding: utf-8 -*-
"""
Daily keyword-filtered news digest → PDF
"""

import os, re, html
from typing import Optional, Tuple, List, Dict
from datetime import datetime, timedelta
from dateutil import tz
import calendar
import feedparser
from rapidfuzz import fuzz
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from functools import lru_cache

# ---------------- CONFIG ----------------
# Not case sensitive!
KEYWORDS = [
    "reform uk", "nigel farage", "richard tice",
    "lee anderson", "danny kruger", "sarah pochin",
    "david bull", "zia yusuf",
]
FUZZY_THRESHOLD = 88

# Surname → required full-name rule
REQUIRE_FULL_IF_TITLE_OR_SUMMARY_CONTAINS = {
    "farage": "nigel farage",
    "yusuf": "zia yusuf",
}

# Body fetch triggers: if any seen in title/summary, fetch body & scan
BODY_TRIGGERS = {
    "farage", "yusuf", "reform", "tice", "anderson", "kruger", "pochin", "bull",
    "ed davey", "lib dem", "liberal democrat", "liberal democrats"
}

NAME_BY_DOMAIN = {
    "theguardian.com": "The Guardian",
    "bbc.co.uk": "BBC", "bbc.com": "BBC",
    "inews.co.uk": "i Paper",
    "telegraph.co.uk": "Telegraph",
    "independent.co.uk": "Independent",
    "dailymail.co.uk": "Daily Mail",
    "express.co.uk": "Daily Express",
    "ft.com": "Financial Times",
    "thetimes.co.uk": "The Times",
    "spectator.co.uk": "Spectator",
    "mirror.co.uk": "Daily Mirror",
    "newstatesman.com": "New Statesman",
    "itv.com": "ITV",
    "cityam.com": "City AM",
    "economist.com": "The Economist",
}

def url_domain(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""

def outlet_name_from_url(url: str) -> str:
    dom = url_domain(url)
    return NAME_BY_DOMAIN.get(dom, dom or "Unknown")


# Article fetch settings
MAX_ARTICLE_BYTES = 2_500_000
ARTICLE_TIMEOUT = 12  # seconds
BODY_FETCH_BUDGET = 80  # total page fetches per run

# Output and formatting
BASE_OUT_DIR = "Reform MPs"
TITLE = "Daily Reform UK MP Mentions"
TIMEZONE = "Europe/London"

# One combined list instead of per-feed sections
GROUP_BY_SOURCE = False
SHOW_SOURCE_BADGE = True

# Filename
USE_DATED_SUBFOLDERS = False
PDF_FILENAME_FORMAT = "{weekday}, {date_dmy}.pdf"

# Limits & layout
MAX_ITEMS_PER_FEED = 120
MAX_RESULTS_PER_DAY = 600
SUMMARY_CHARS = 320
# ---------------------------------------

FEEDS = {
    # BBC
    "BBC (UK)": "https://feeds.bbci.co.uk/news/uk/rss.xml",
    "BBC (Politics)": "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "BBC (World)": "https://feeds.bbci.co.uk/news/world/rss.xml",

    # Daily Mail
    "Daily Mail (UK News)": "https://www.dailymail.co.uk/news/index.rss",
    "Daily Mail (Articles)": "https://www.dailymail.co.uk/articles.rss",
    "Daily Mail (Politics)": "https://www.dailymail.co.uk/news/politics/index.rss",

    # Telegraph
    "Telegraph (UK News)": "https://www.telegraph.co.uk/news/rss.xml",
    "Telegraph (Politics)": "https://www.telegraph.co.uk/politics/rss.xml",
    "Telegraph (World News)": "https://www.telegraph.co.uk/world-news/rss.xml",

    # The Times (fallback via Google)
    "The Times (Google)": "https://news.google.com/rss/search?q=site:thetimes.co.uk",

    # Financial Times
    "Financial Times (UK)": "https://www.ft.com/rss/uk",
    "Financial Times (World)": "https://www.ft.com/rss/world",

    # Daily Express
    "Daily Express (News)": "https://www.express.co.uk/posts/rss/1",
    "Daily Express (Politics)": "https://www.express.co.uk/posts/rss/77",

    # Spectator
    "Spectator (Politics)": "https://www.spectator.co.uk/feed",

    # The Guardian
    "Guardian (UK News)": "https://www.theguardian.com/uk-news/rss",
    "Guardian (Politics)": "https://www.theguardian.com/politics/rss",

    # Daily Mirror
    "Daily Mirror (News)": "https://www.mirror.co.uk/news/rss.xml",

    # New Statesman
    "New Statesman (Politics)": "https://www.newstatesman.com/politics/feed",

    # ITV
    "ITV": "http://www.itv.com/news/index.rss",
    "ITV (Google News)": "https://news.google.com/rss/search?q=site:itv.com/news",

    # Independent
    "Independent (News)": "https://www.independent.co.uk/news/rss",
    "Independent (Politics)": "https://www.independent.co.uk/news/uk/politics/rss",

    # i Paper (use real sources)
    "i Paper (Google News)": "https://news.google.com/rss/search?q=site:inews.co.uk",
    "i Paper (Site Feed)": "https://inews.co.uk/feed",

    # City AM
    "City AM (News)": "https://www.cityam.com/feed",

    # Economist (summaries; paywalled)
    "Economist (Latest)": "https://www.economist.com/latest/rss.xml",
}

# --- FEED LABEL NORMALIZATION ---
FEEDS = {k.strip(): v for k, v in FEEDS.items()}
FEED_ORDER = sorted(FEEDS.keys())

# Friendly outlet names by domain
NAME_BY_DOMAIN: Dict[str, str] = {
    "bbc.co.uk": "BBC", "bbc.com": "BBC",
    "dailymail.co.uk": "Daily Mail",
    "telegraph.co.uk": "Telegraph",
    "thetimes.co.uk": "The Times",
    "ft.com": "Financial Times",
    "express.co.uk": "Daily Express",
    "spectator.co.uk": "Spectator",
    "theguardian.com": "The Guardian",
    "mirror.co.uk": "Daily Mirror",
    "newstatesman.com": "New Statesman",
    "itv.com": "ITV",
    "independent.co.uk": "Independent",
    "inews.co.uk": "i Paper",
    "cityam.com": "City AM",
    "economist.com": "The Economist",
}

# ---------------- HELPERS ----------------
def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style|noscript|template|svg|iframe|picture|source).*?>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def norm(s):
    return (s or "").strip()

def contains_word(text: str, needle: str) -> bool:
    return re.search(rf"\b{re.escape(needle)}\b", text, re.IGNORECASE) is not None

def outlet_name_from_url(url: str) -> str:
    dom = url_domain(url)
    return NAME_BY_DOMAIN.get(dom, dom or "Unknown")

@lru_cache(maxsize=512)
def fetch_article_text(url: str) -> str:
    """Fetch article HTML, return plain text."""
    if not url:
        return ""
    try:
        # Realistic UA helps with BBC + others
        req = Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })
        with urlopen(req, timeout=ARTICLE_TIMEOUT) as resp:
            raw = resp.read(MAX_ARTICLE_BYTES)
            ctype = resp.headers.get("Content-Type", "")
            m = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype)
            enc = (m.group(1) if m else "utf-8").strip()
            try:
                html_text = raw.decode(enc, errors="ignore")
            except LookupError:
                html_text = raw.decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError, Exception):
        return ""
    return strip_html(html_text).lower()

def _full_name_satisfied(article_text: str, full_name: str) -> bool:
    t = (article_text or "").lower()
    if not t:
        return False
    if re.search(rf"\b{re.escape(full_name)}\b", t):
        return True
    if "farage" in full_name and re.search(r"\bmr\s+farage\b", t):
        return True
    if "farage" in full_name:
        if re.search(r"\bnigel\b.{0,40}\bfarage\b", t) or re.search(r"\bfarage\b.{0,40}\bnigel\b", t):
            return True
    if "yusuf" in full_name:
        if re.search(r"\bzia\b.{0,40}\byusuf\b", t) or re.search(r"\byusuf\b.{0,40}\bzia\b", t):
            return True
    return False

def _body_matches_targets(article_text: str) -> Tuple[bool, Optional[str]]:
    t = (article_text or "").lower()
    if not t:
        return (False, None)
    # Party phrase
    if re.search(r"\breform\s*uk\b", t) or re.search(r"\breform\b.{0,12}\bparty\b", t):
        return (True, "reform uk")
    # Named people
    for full in ["nigel farage", "richard tice", "lee anderson", "danny kruger", "sarah pochin", "david bull", "zia yusuf"]:
        if "farage" in full or "yusuf" in full:
            if _full_name_satisfied(t, full):
                return (True, full)
        elif re.search(rf"\b{re.escape(full)}\b", t):
            return (True, full)
    return (False, None)

def extract_google_target(u: str) -> str:
    try:
        netloc = (urlsplit(u).netloc or "").lower()
        if "news.google.com" not in netloc:
            return u
        qs = parse_qs(urlsplit(u).query)
        real = (qs.get("url") or qs.get("u") or [None])[0]
        return real or u
    except Exception:
        return u

# ----------- DEDUPE HELPERS -----------
_TRACKING_PREFIXES = ("utm_", "gclid", "gclsrc", "fbclid", "at_", "ns_", "ito", "cmp", "icid", "ref")

def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        s = urlsplit(url.strip())
        scheme = "https" if s.scheme in ("http", "https", "") else s.scheme
        netloc = s.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = re.sub(r"/+$", "", s.path) or "/"
        keep_keys = {"id", "p", "story", "article"}
        params = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)]
        params = [(k, v) for (k, v) in params if not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)]
        params = [(k, v) for (k, v) in params if k in keep_keys]
        query = urlencode(params)
        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return url

def url_domain(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""

def title_fingerprint(title: str) -> str:
    t = (title or "").lower()
    t = t.replace("&amp;", "&")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

# ---------------- MATCHING ----------------
def find_matching_keywords(title: str, summary: str, url: str) -> Tuple[bool, Optional[str]]:
    """
    1) Try KEYWORDS on title+summary (exact/fuzzy).
    2) If title OR summary contains surname triggers (Farage/Yusuf), fetch and require robust full-name.
    3) If title+summary contain any BODY_TRIGGERS (incl. Ed Davey / Lib Dem), fetch and scan BODY.
       - For BBC domains, we are more willing to fetch on triggers due to terse summaries.
    """
    global BODY_FETCH_BUDGET

    title_l = (title or "").lower()
    
    summary_l = (summary or "").lower()
    combined = f"{title_l}\n{summary_l}"
    domain = url_domain(url)

    # 1) Normal keyword matching on title+summary
    for kw in KEYWORDS:
        kwl = kw.lower()
        if re.search(rf"\b{re.escape(kwl)}\b", combined):
            return True, kw
        if fuzz.partial_ratio(kwl, combined) >= FUZZY_THRESHOLD:
            return True, kw

    # 2) Surname → full-name rule (TITLE or SUMMARY)
    for surname, full_name in REQUIRE_FULL_IF_TITLE_OR_SUMMARY_CONTAINS.items():
        if contains_word(title_l, surname) or contains_word(summary_l, surname):
            if contains_word(summary_l, full_name):
                return True, full_name
            if BODY_FETCH_BUDGET > 0:
                BODY_FETCH_BUDGET -= 1
                article_text = fetch_article_text(url)
                if _full_name_satisfied(article_text, full_name):
                    return True, full_name
            return False, None  # have surname in T/S but body didn't satisfy

    # 3) Body-trigger fallback
    if any(contains_word(combined, trig) for trig in BODY_TRIGGERS):
        # BBC boost: their summaries are terse; allow fetch budget priority
        if BODY_FETCH_BUDGET > 0 or domain.endswith("bbc.co.uk") or domain.endswith("bbc.com"):
            if BODY_FETCH_BUDGET > 0:
                BODY_FETCH_BUDGET -= 1
            article_text = fetch_article_text(url)
            ok, hit = _body_matches_targets(article_text)
            if ok:
                return True, hit

    return False, None

# ---------------- PIPELINE ----------------
def fetch_feed(name, url, start_utc, end_utc):
    parsed = feedparser.parse(url)
    out = []
    for e in parsed.entries[:MAX_ITEMS_PER_FEED]:
        struct = e.get("published_parsed") or e.get("updated_parsed")
        if not struct:
            continue
        dt_utc = datetime.utcfromtimestamp(calendar.timegm(struct)).replace(tzinfo=tz.UTC)
        if not (start_utc <= dt_utc < end_utc):
            continue

        title_raw   = norm(e.get("title"))
        summary_raw = norm(e.get("summary", "")) or norm(e.get("description", ""))
        link_raw    = norm(e.get("link"))
        link        = extract_google_target(link_raw)  # handle Google News wrappers
        pub_str     = norm(e.get("published", e.get("updated", "")))

        ok, hit_kw = find_matching_keywords(title_raw, summary_raw, link)
        if not ok:
            continue

        out.append({
            "source": name,
            "title": strip_html(title_raw),
            "summary": strip_html(summary_raw)[:SUMMARY_CHARS],
            "link": link,
            "published": pub_str,
            "dt_sort": dt_utc.timestamp(),
            "hit": hit_kw,
        })
    return out

def dedupe(items):
    """Dedupe by canonical URL and title per outlet domain."""
    seen_urls = set()
    seen_titles_by_domain = {}
    result = []
    for it in items:
        url = it.get("link", "")
        can = canonical_url(url)
        if can:
            if can in seen_urls:
                continue
            seen_urls.add(can)

        domain = url_domain(can or url)
        tfp = title_fingerprint(it.get("title", ""))

        bucket = seen_titles_by_domain.setdefault(domain, set())
        if tfp in bucket:
            continue
        bucket.add(tfp)

        result.append(it)
    return result

def highlight_title(title: str, hit_kw: Optional[str]) -> str:
    safe = html.escape(title or "(no title)")
    if hit_kw:
        pattern = re.compile(re.escape(hit_kw), re.IGNORECASE)
        safe = pattern.sub(r"<b>\g<0></b>", safe)
    return safe

def make_pdf(items, path, run_dt_local):
    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]; h1.fontSize = 18; h1.spaceAfter = 12
    h2 = styles["Heading2"]; h2.fontSize = 12; h2.textColor = colors.HexColor("#666666"); h2.spaceAfter = 8

    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=14, alignment=TA_LEFT, spaceAfter=6)
    link_style = ParagraphStyle("link", parent=styles["BodyText"], fontSize=9, textColor=colors.blue, leading=12, spaceAfter=10)
    title_line = ParagraphStyle("title_line", parent=body, fontName="Helvetica-Bold")  # <- bold headlines

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    flow = []

    # Header
    weekday = run_dt_local.strftime('%A')
    flow.append(Paragraph(TITLE, h1))
    sub = f"Generated {weekday}, {run_dt_local.strftime('%Y-%m-%d %H:%M')} ({TIMEZONE}) · {len(items)} matches · Sources: {len(FEEDS)}"
    flow.append(Paragraph(sub, h2))
    flow.append(Spacer(1, 8))

    if GROUP_BY_SOURCE:
        # Optional grouped view (kept just in case you toggle it later)
        by_src = {}
        for it in items:
            by_src.setdefault(it["source"], []).append(it)

        for src in FEED_ORDER:
            flow.append(Paragraph(src, styles["Heading3"]))
            items_for_src = sorted(by_src.get(src, []), key=lambda x: x.get("dt_sort", 0), reverse=True)
            if not items_for_src:
                flow.append(Paragraph("<i>—</i>", body))
                flow.append(Spacer(1, 8))
                continue

            for it in items_for_src:
                badge = f"[{outlet_name_from_url(it.get('link',''))}] " if SHOW_SOURCE_BADGE else ""
                headline = badge + (it.get("title") or "(no title)")
                title_markup = highlight_title(headline, it.get("hit"))
                flow.append(Paragraph(title_markup, title_line))  # bold + badge

                if it.get("published"):
                    flow.append(Paragraph(f"<i>{html.escape(it['published'])}</i>", body))
                if it.get("summary"):
                    flow.append(Paragraph(html.escape(it["summary"]), body))
                if it.get("link"):
                    flow.append(Paragraph(f'<a href="{html.escape(it["link"])}">{html.escape(it["link"])}</a>', link_style))
                flow.append(Spacer(1, 6))
            flow.append(Spacer(1, 8))
    else:
        # Default: single combined list
        flow.append(Paragraph("All sources", styles["Heading3"]))
        items_sorted = sorted(items, key=lambda x: x.get("dt_sort", 0), reverse=True)

        if not items_sorted:
            flow.append(Paragraph("<i>No matches today.</i>", body))

        for it in items_sorted:
            badge = f"[{outlet_name_from_url(it.get('link',''))}] " if SHOW_SOURCE_BADGE else ""
            headline = badge + (it.get("title") or "(no title)")
            title_markup = highlight_title(headline, it.get("hit"))
            flow.append(Paragraph(title_markup, title_line))  # bold + badge

            if it.get("published"):
                flow.append(Paragraph(f"<i>{html.escape(it['published'])}</i>", body))
            if it.get("summary"):
                flow.append(Paragraph(html.escape(it["summary"]), body))
            if it.get("link"):
                flow.append(Paragraph(f'<a href="{html.escape(it["link"])}">{html.escape(it["link"])}</a>', link_style))
            flow.append(Spacer(1, 6))

    def on_page(canvas_, doc_):
        from reportlab.lib.pagesizes import A4 as PAGESIZE
        canvas_.saveState()
        footer = f"{TITLE} · {weekday}, {run_dt_local.strftime('%Y-%m-%d')} · Page {doc_.page}"
        canvas_.setFont("Helvetica", 8)
        canvas_.setFillColor(colors.grey)
        canvas_.drawRightString(PAGESIZE[0] - 2*cm, 1.2*cm, footer)
        canvas_.restoreState()

    doc.build(flow, onFirstPage=on_page, onLaterPages=on_page)


def main():
    london = tz.gettz(TIMEZONE)
    now_local = datetime.now(tz=london)

    # Today's window in London → UTC
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local   = start_local + timedelta(days=1)
    start_utc   = start_local.astimezone(tz.UTC)
    end_utc     = end_local.astimezone(tz.UTC)

    global BODY_FETCH_BUDGET
    BODY_FETCH_BUDGET = 80

    all_items: List[Dict] = []
    for name, url in FEEDS.items():
        try:
            all_items.extend(fetch_feed(name, url, start_utc, end_utc))
        except Exception as ex:
            print(f"[warn] {name}: {ex}")

    all_items = dedupe(all_items)[:MAX_RESULTS_PER_DAY]

    # Where to save
    if USE_DATED_SUBFOLDERS:
        out_dir = os.path.join(BASE_OUT_DIR, f"{now_local.strftime('%Y-%m-%d')}_{now_local.strftime('%a')}")
    else:
        out_dir = BASE_OUT_DIR
    ensure_dir(out_dir)

    # Filename: e.g., "Tuesday, 23-09-2025.pdf"
    fname = PDF_FILENAME_FORMAT.format(
        weekday=now_local.strftime('%A'),
        date_dmy=now_local.strftime('%d-%m-%Y')
    )
    fpath = os.path.join(out_dir, fname)

    make_pdf(all_items, fpath, now_local)
    print(f"Saved {len(all_items)} matches → {fpath}")

if __name__ == "__main__":
    main()
