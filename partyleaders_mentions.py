# -*- coding: utf-8 -*-
"""
Daily keyword-filtered news digest → PDF
(Party Leaders Mentions) — improved matching & coverage
"""

import os, re, html, unicodedata
from typing import Optional, List, Dict
from datetime import datetime, timedelta, date
from dateutil import tz
import calendar, time
import feedparser
from rapidfuzz import fuzz
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote_plus
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# ---------------- CONFIG ----------------
# Canonical leaders mapped to aliases for matching (display uses canonical names)
LEADER_ALIASES: Dict[str, List[str]] = {
    "nigel farage": ["nigel farage", "farage"],                  # Reform UK
    "keir starmer": ["keir starmer", "starmer"],                 # Labour
    "kemi badenoch": ["kemi badenoch", "badenoch"],              # Conservative
    "john swinney": ["john swinney", "swinney"],                 # SNP
    "ed davey": ["ed davey", "davey"],                           # Lib Dems
    "rhun ap iorwerth": ["rhun ap iorwerth", "ap iorwerth", "iorwerth"], # Plaid
    "mary lou mcdonald": ["mary lou mcdonald", "mcdonald"],      # Sinn Féin (surname uncommon in UK politics)
    "gavin robinson": ["gavin robinson"],                        # DUP (surname too generic to include alone)
}
CANONICAL = list(LEADER_ALIASES.keys())

# Base feeds (we will supplement with Google News per-leader for Daily Mail)
BASE_FEEDS = {
    # Daily Mail (native feeds can miss items; we add Google News per-leader below)
    "Daily Mail (UK News)": "https://www.dailymail.co.uk/news/index.rss",
    "Daily Mail (Articles)": "https://www.dailymail.co.uk/articles.rss",
    "Daily Mail (Politics)": "https://www.dailymail.co.uk/news/politics/index.rss",

    # Telegraph
    "Telegraph (UK News)": "https://www.telegraph.co.uk/news/rss.xml",
    "Telegraph (Politics)": "https://www.telegraph.co.uk/politics/rss.xml",
    "Telegraph (World News)": "https://www.telegraph.co.uk/world-news/rss.xml",

    # The Times (use Google News as fallback)
    "The Times (Google)": "https://news.google.com/rss/search?q=site:thetimes.co.uk",

    # Financial Times (summaries; paywalled)
    "Financial Times (UK)": "https://www.ft.com/rss/uk",
    "Financial Times (World)": "https://www.ft.com/rss/world",

    # Daily Express
    "Daily Express (News)": "https://www.express.co.uk/posts/rss/1",
    "Daily Express (Politics)": "https://www.express.co.uk/posts/rss/77",

    # Spectator (paywall may apply)
    "Spectator (Politics)": "https://www.spectator.co.uk/feed",

    # The Guardian
    "Guardian (UK News)": "https://www.theguardian.com/uk-news/rss",
    "Guardian (Politics)": "https://www.theguardian.com/politics/rss",

    # Daily Mirror
    "Daily Mirror (News)": "https://www.mirror.co.uk/news/rss.xml",

    # New Statesman – limited feed
    "New Statesman (Politics)": "https://www.newstatesman.com/politics/feed",

    # BBC
    "BBC (UK)": "https://feeds.bbci.co.uk/news/uk/rss.xml",
    "BBC (Politics)": "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "BBC (World)": "https://feeds.bbci.co.uk/news/world/rss.xml",

    # ITV (native feed may be unreliable; Google fallback included)
    "ITV": "http://www.itv.com/news/index.rss",
    "ITV (Google News)": "https://news.google.com/rss/search?q=site:itv.com/news",

    # Independent
    "Independent (News)": "https://www.independent.co.uk/news/rss",
    "Independent (Politics)": "https://www.independent.co.uk/news/uk/politics/rss",

    # i Paper via Google News
    "i Paper (Google News)": "https://news.google.com/rss/search?q=site:inews.co.uk",

    # City AM
    "City AM (News)": "https://www.cityam.com/feed",

    # Economist (summaries; paywalled)
    "Economist (Latest)": "https://www.economist.com/latest/rss.xml",
}

# Output folder and title
BASE_OUT_DIR = "Party Leaders"
TITLE = "Daily UK Party Leaders Mentions"
TIMEZONE = "Europe/London"

# Filename formatting
PDF_FILENAME_FORMAT = "{weekday}, {date_dmy}.pdf"  # e.g. "Monday, 22-09-2025.pdf"

# Matching/collection parameters
FUZZY_THRESHOLD = 80          # was 88 — loosened to catch variants
MAX_ITEMS_PER_FEED = 300      # was 120 — busy days can exceed this
MAX_RESULTS_PER_DAY = 500
SUMMARY_CHARS = 320
EMPTY_SECTION_LINES = 1
# ---------------------------------------

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def strip_html(s: str) -> str:
    if not s: return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def norm(s): return (s or "").strip()

def normalize_text(s: str) -> str:
    """Unescape HTML, normalize Unicode, collapse spaces, lowercase."""
    s = html.unescape(s or "")
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'")  # normalize curly apostrophes
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

# ---------- Matching ----------
def find_all_hits(title_raw: str, summary_raw: str) -> List[str]:
    text = normalize_text(f"{title_raw}\n{summary_raw}")
    hits = []
    for canonical, aliases in LEADER_ALIASES.items():
        for a in aliases:
            a_norm = normalize_text(a)
            # strict word-boundary match first
            if re.search(rf"\b{re.escape(a_norm)}\b", text):
                hits.append(canonical); break
            # fuzzy backup
            if fuzz.partial_ratio(a_norm, text) >= FUZZY_THRESHOLD:
                hits.append(canonical); break
    return hits

# ----------- DEDUPE HELPERS -----------
_TRACKING_PREFIXES = ("utm_", "gclid", "gclsrc", "fbclid", "at_", "ns_", "ito", "cmp", "icid", "ref")

def canonical_url(url: str) -> str:
    if not url: return ""
    try:
        s = urlsplit(url.strip())
        scheme = "https" if s.scheme in ("http", "https", "") else s.scheme
        netloc = s.netloc.lower()
        if netloc.startswith("www."): netloc = netloc[4:]
        path = re.sub(r"/+$", "", s.path) or "/"
        keep_keys = {"id","p","story","article"}
        params = [(k,v) for k,v in parse_qsl(s.query, keep_blank_values=True)]
        params = [(k,v) for (k,v) in params if not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)]
        params = [(k,v) for (k,v) in params if k in keep_keys]
        query = urlencode(params)
        return urlunsplit((scheme, netloc, path, query, ""))
    except:
        return url

def url_domain(url: str) -> str:
    try: return (urlsplit(url).netloc or "").lower().lstrip("www.")
    except: return ""

def title_fingerprint(title: str) -> str:
    t = (title or "").lower().replace("&amp;","&")
    t = re.sub(r"[^a-z0-9]+"," ",t)
    return re.sub(r"\s+"," ",t).strip()

def dedupe(items):
    seen_urls=set(); seen_titles_by_domain={}; seen_titles_global=set(); result=[]
    for it in items:
        url=it.get("link",""); can=canonical_url(url)
        if can:
            if can in seen_urls: continue
            seen_urls.add(can)
        domain=url_domain(can or url)
        tfp=title_fingerprint(it.get("title",""))
        if domain=="news.google.com" or not domain:
            if tfp in seen_titles_global: continue
            seen_titles_global.add(tfp)
        else:
            bucket=seen_titles_by_domain.setdefault(domain,set())
            if tfp in bucket: continue
            bucket.add(tfp)
        result.append(it)
    return result

# ----------- FEEDS -----------
def build_feeds() -> Dict[str, str]:
    """Add Google News per-leader for Daily Mail to catch missing native RSS items."""
    feeds = dict(BASE_FEEDS)
    for leader, aliases in LEADER_ALIASES.items():
        query = f"site:dailymail.co.uk {aliases[0]}"  # use the most specific alias (full name)
        gn = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-GB&gl=GB&ceid=GB:en"
        feeds[f"Daily Mail via Google ({leader})"] = gn
    return feeds

# ----------- FETCH -----------
def fetch_feed(name: str, url: str, london_tz, target_date: date):
    parsed = feedparser.parse(url); out=[]
    for e in parsed.entries[:MAX_ITEMS_PER_FEED]:
        struct = e.get("published_parsed") or e.get("updated_parsed")
        if not struct: 
            continue
        dt_utc = datetime.utcfromtimestamp(calendar.timegm(struct)).replace(tzinfo=tz.UTC)
        dt_lon = dt_utc.astimezone(london_tz)
        if dt_lon.date() != target_date:
            continue

        # Keep original strings for display
        title_raw = norm(e.get("title"))
        summary_raw = norm(e.get("summary","")) or norm(e.get("description",""))
        link = norm(e.get("link"))
        pub = norm(e.get("published", e.get("updated","")))

        # Matching is done on normalized text
        hits = find_all_hits(title_raw, summary_raw)
        if not hits: 
            continue

        out.append({
            "source": name,
            "title": strip_html(title_raw),
            "summary": strip_html(summary_raw)[:SUMMARY_CHARS],
            "link": link,
            "published": pub,
            "hits": hits
        })
    return out

# ----------- PDF RENDERING -----------
def highlight_title_for_leader(title: str, leader_canonical: Optional[str]) -> str:
    """Bold any alias occurrences for the matched leader in the title."""
    safe = html.escape(title)
    if leader_canonical and leader_canonical in LEADER_ALIASES:
        aliases = sorted(LEADER_ALIASES[leader_canonical], key=len, reverse=True)
        for alias in aliases:
            pattern = re.compile(re.escape(alias), re.IGNORECASE)
            safe = pattern.sub(r"<b>\g<0></b>", safe)
    return safe

def make_pdf(items, path, run_dt_local, num_sources: int):
    styles=getSampleStyleSheet()
    h1=styles["Heading1"]; h1.fontSize=18; h1.spaceAfter=12
    h2=styles["Heading2"]; h2.fontSize=12; h2.textColor=colors.HexColor("#666666"); h2.spaceAfter=8
    h3=styles["Heading3"]; h3.fontSize=13; h3.spaceBefore=8; h3.spaceAfter=6
    body=ParagraphStyle("body",parent=styles["BodyText"],fontSize=10,leading=14,alignment=TA_LEFT,spaceAfter=4)
    meta=ParagraphStyle("meta",parent=styles["BodyText"],fontSize=9,textColor=colors.HexColor("#555555"),leading=12,spaceAfter=2)
    link_style=ParagraphStyle("link",parent=styles["BodyText"],fontSize=9,textColor=colors.blue,leading=12,spaceAfter=8)

    doc=SimpleDocTemplate(path,pagesize=A4,leftMargin=2*cm,rightMargin=2*cm,topMargin=2*cm,bottomMargin=2*cm)
    flow=[]
    weekday=run_dt_local.strftime('%A')
    flow.append(Paragraph(TITLE,h1))
    sub=f"Generated {weekday}, {run_dt_local.strftime('%Y-%m-%d %H:%M')} ({TIMEZONE}) · {len(items)} total matches · Sources: {num_sources}"
    flow.append(Paragraph(sub,h2)); flow.append(Spacer(1,6))

    items_by_leader={kw:[] for kw in CANONICAL}
    for it in items:
        for kw in it.get("hits",[]):
            items_by_leader.setdefault(kw, []).append(it)

    for leader in CANONICAL:
        section_items=items_by_leader.get(leader,[])
        flow.append(Paragraph(leader.title(),h3))
        if not section_items:
            for _ in range(EMPTY_SECTION_LINES): 
                flow.append(Paragraph("<i>— no mentions today —</i>",body))
            flow.append(Spacer(1,6)); 
            continue

        section_items_sorted=sorted(section_items,key=lambda x:x.get("published",""),reverse=True)
        for it in section_items_sorted:
            flow.append(Paragraph(highlight_title_for_leader(it.get("title") or "(no title)", leader), body))
            meta_bits=[]
            if it.get("source"): meta_bits.append(it["source"])
            if it.get("published"): meta_bits.append(it["published"])
            if meta_bits: flow.append(Paragraph(" · ".join(html.escape(b) for b in meta_bits),meta))
            if it.get("summary"): flow.append(Paragraph(html.escape(it["summary"]),body))
            if it.get("link"):
                link_html=f'<a href="{html.escape(it["link"])}">{html.escape(it["link"])}</a>'
                flow.append(Paragraph(link_html,link_style))
        flow.append(Spacer(1,8))

    def on_page(canvas_,doc_):
        from reportlab.lib.pagesizes import A4 as PAGESIZE
        canvas_.saveState()
        footer=f"{TITLE} · {weekday}, {run_dt_local.strftime('%Y-%m-%d')} · Page {doc_.page}"
        canvas_.setFont("Helvetica",8); canvas_.setFillColor(colors.grey)
        canvas_.drawRightString(PAGESIZE[0]-2*cm,1.2*cm,footer); canvas_.restoreState()
    doc.build(flow,onFirstPage=on_page,onLaterPages=on_page)

# ----------- MAIN -----------
def main():
    london=tz.gettz(TIMEZONE)
    now_local=datetime.now(tz=london)
    today_lon=now_local.date()

    feeds = build_feeds()  # includes base + Daily Mail via Google per leader

    all_items=[]
    for name,url in feeds.items():
        try:
            all_items.extend(fetch_feed(name,url,london,today_lon))
        except Exception as ex:
            print(f"[warn] {name}: {ex}")

    all_items = dedupe(all_items)[:MAX_RESULTS_PER_DAY]

    # Save directly in BASE_OUT_DIR (no subfolders)
    ensure_dir(BASE_OUT_DIR)
    weekday=now_local.strftime('%A')
    date_dmy=now_local.strftime('%d-%m-%Y')
    fname=PDF_FILENAME_FORMAT.format(weekday=weekday,date_dmy=date_dmy)
    fpath=os.path.join(BASE_OUT_DIR,fname)

    make_pdf(all_items,fpath,now_local,num_sources=len(feeds))
    print(f"Saved {len(all_items)} matches → {fpath}")

if __name__=="__main__": 
    main()
