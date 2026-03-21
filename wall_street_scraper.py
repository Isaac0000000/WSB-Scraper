#!/usr/bin/env python3
"""News Terminal Dashboard — search top investment bank insights."""

import re
import webbrowser
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, Response

# ── Config ───────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 15

BANKS = {
    "JPMorgan": {
        "color": "#003A70",
        "urls": [
            "https://www.jpmorgan.com/insights",
            "https://www.jpmorgan.com/insights/research",
            "https://www.jpmorgan.com/insights/markets",
            "https://www.jpmorgan.com/insights/economy",
        ],
        "base": "https://www.jpmorgan.com",
    },
    "Goldman Sachs": {
        "color": "#4A6FA5",
        "urls": [
            "https://www.goldmansachs.com/insights/articles",
            "https://www.goldmansachs.com/insights",
        ],
        "base": "https://www.goldmansachs.com",
    },
    "Morgan Stanley": {
        "color": "#002F6C",
        "urls": [
            "https://www.morganstanley.com/ideas",
            "https://www.morganstanley.com/insights",
        ],
        "base": "https://www.morganstanley.com",
    },
    "Citi": {
        "color": "#003B70",
        "urls": [
            "https://www.citigroup.com/global/insights",
            "https://www.citigroup.com/global/insights/all",
        ],
        "base": "https://www.citigroup.com",
    },
    "HSBC": {
        "color": "#DB0011",
        "urls": [
            "https://www.hsbc.com/insight",
            "https://www.hsbc.com/news-and-views",
            "https://www.hsbc.com/news-and-views/news",
            "https://www.hsbc.com/news-and-views/views",
        ],
        "base": "https://www.hsbc.com",
    },
    "RBC": {
        "color": "#003DA5",
        "urls": [
            "https://www.rbccm.com/en/insights.page",
        ],
        "base": "https://www.rbccm.com",
    },
    "ZeroHedge": {
        "color": "#F57C00",
        "urls": [
            "https://www.zerohedge.com/markets",
            "https://www.zerohedge.com/economics",
            "https://www.zerohedge.com/commodities",
        ],
        "base": "https://www.zerohedge.com",
    },
}

# ── Helpers ──────────────────────────────────────────────────────────────────

DATE_PATTERNS = [
    r"(\w+ \d{1,2},?\s*\d{4})",           # March 10, 2026 / Mar 10 2026
    r"(\d{1,2} \w+ \d{4})",               # 10 March 2026
    r"(\d{4}-\d{2}-\d{2})",               # 2026-03-10
    r"(\d{1,2}/\d{1,2}/\d{2,4})",         # 3/10/2026
]

DATE_FORMATS = [
    "%B %d, %Y",    # March 10, 2026
    "%B %d %Y",     # March 10 2026
    "%b %d, %Y",    # Mar 10, 2026
    "%b %d %Y",     # Mar 10 2026
    "%d %B %Y",     # 10 March 2026
    "%d %b %Y",     # 10 Mar 2026
    "%Y-%m-%d",     # 2026-03-10
    "%m/%d/%Y",     # 3/10/2026
    "%m/%d/%y",     # 3/10/26
]


def _fetch(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    except Exception:
        return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_SKIP_TITLES = {"read more", "learn more", "view all", "see more", "load more",
                 "view all insights", "view all reports", "explore more",
                 "read now", "listen now", "watch now", "subscribe"}


def _dedupe(articles: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for a in articles:
        key = a["url"]
        if key in seen:
            continue
        if a["title"].lower().strip() in _SKIP_TITLES:
            continue
        seen.add(key)
        out.append(a)
    return out


def _parse_date(text: str) -> str | None:
    """Try to parse a date string into YYYY-MM-DD format."""
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_date_from_text(text: str) -> str | None:
    """Find and parse a date from a block of text."""
    for pattern in DATE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            result = _parse_date(m.group(1))
            if result:
                return result
    return None


def _extract_date_from_soup(soup: BeautifulSoup) -> str | None:
    """Extract a publish date from a page's meta tags or common date elements."""
    # Try meta tags first (most reliable)
    for attr in ["article:published_time", "datePublished", "date", "DC.date.issued"]:
        tag = soup.find("meta", attrs={"property": attr}) or soup.find("meta", attrs={"name": attr})
        if tag and tag.get("content"):
            raw = tag["content"][:10]  # YYYY-MM-DD
            parsed = _parse_date(raw)
            if parsed:
                return parsed

    # Try JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or ""
        for key in ['"datePublished"', '"dateCreated"']:
            idx = text.find(key)
            if idx != -1:
                # grab the value after the key
                val_start = text.find('"', idx + len(key) + 1)
                if val_start != -1:
                    val_end = text.find('"', val_start + 1)
                    if val_end != -1:
                        raw = text[val_start + 1:val_end][:10]
                        parsed = _parse_date(raw)
                        if parsed:
                            return parsed

    # Try time tags
    for time_tag in soup.find_all("time"):
        dt = time_tag.get("datetime", "") or time_tag.get_text()
        parsed = _parse_date(dt[:10])
        if parsed:
            return parsed

    return None


def _find_date_near_link(a_tag) -> str | None:
    """Try to find a date near an article link in the listing page."""
    parent = a_tag.find_parent(["article", "div", "li", "section"])
    if not parent:
        return None
    # Look for time tags
    time_tag = parent.find("time")
    if time_tag:
        dt = time_tag.get("datetime", "") or time_tag.get_text()
        parsed = _parse_date(dt[:10])
        if parsed:
            return parsed
    # Look for date-like text in sibling/child elements
    text = parent.get_text(" ", strip=True)
    return _extract_date_from_text(text)


def _fetch_article_date(url: str) -> str | None:
    """Fetch an individual article page to extract its publish date."""
    soup = _fetch(url)
    if not soup:
        return None
    return _extract_date_from_soup(soup)


def _enrich_dates(articles: list[dict], max_fetches: int = 8) -> list[dict]:
    """For articles missing dates, fetch the article page to get the date.
    Limit fetches to avoid being too slow."""
    missing = [a for a in articles if not a.get("date")]
    to_fetch = missing[:max_fetches]
    if not to_fetch:
        return articles

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_article_date, a["url"]): a for a in to_fetch}
        for future in as_completed(futures):
            article = futures[future]
            try:
                date = future.result()
                if date:
                    article["date"] = date
            except Exception:
                pass
    return articles


# ── JPMorgan ─────────────────────────────────────────────────────────────────

def scrape_jpmorgan() -> list[dict]:
    articles = []
    for url in BANKS["JPMorgan"]["urls"]:
        soup = _fetch(url)
        if not soup:
            continue
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "/insights/" not in href:
                continue
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) < 3:
                continue
            title = _clean(a_tag.get_text())
            if not title or len(title) < 10:
                parent = a_tag.find_parent(["article", "div", "li", "section"])
                if parent:
                    heading = parent.find(["h1", "h2", "h3", "h4", "h5"])
                    if heading:
                        title = _clean(heading.get_text())
            skip_titles = {"read more", "learn more", "view all", "see more", "load more",
                           "view all insights", "view all reports", "explore more"}
            if not title or len(title) < 10 or title.lower().strip() in skip_titles:
                continue
            full_url = urljoin(BANKS["JPMorgan"]["base"], href)
            snippet = ""
            date = _find_date_near_link(a_tag)
            parent = a_tag.find_parent(["article", "div", "li", "section"])
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    snippet = _clean(p_tag.get_text())[:200]
            articles.append({
                "source": "JPMorgan",
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "date": date or "",
            })
    return _dedupe(articles)


# ── Goldman Sachs ────────────────────────────────────────────────────────────

def scrape_goldman() -> list[dict]:
    articles = []
    for url in BANKS["Goldman Sachs"]["urls"]:
        soup = _fetch(url)
        if not soup:
            continue
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "/insights/" not in href:
                continue
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) < 3:
                continue
            if href.endswith("/insights/articles") or href.endswith("/insights/"):
                continue
            title = _clean(a_tag.get_text())
            if not title or len(title) < 10:
                parent = a_tag.find_parent(["article", "div", "li", "section"])
                if parent:
                    heading = parent.find(["h1", "h2", "h3", "h4", "h5"])
                    if heading:
                        title = _clean(heading.get_text())
            if not title or len(title) < 10:
                slug = parts[-1]
                title = slug.replace("-", " ").title()
            full_url = urljoin(BANKS["Goldman Sachs"]["base"], href)
            snippet = ""
            date = _find_date_near_link(a_tag)
            parent = a_tag.find_parent(["article", "div", "li", "section"])
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    snippet = _clean(p_tag.get_text())[:200]
            articles.append({
                "source": "Goldman Sachs",
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "date": date or "",
            })

    # Goldman Sachs is JS-heavy; also try sitemap as fallback
    if len(articles) < 5:
        articles.extend(_scrape_goldman_sitemap())

    return _dedupe(articles)


def _scrape_goldman_sitemap() -> list[dict]:
    articles = []
    try:
        r = requests.get(
            "https://www.goldmansachs.com/sitemap-1.xml",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml-xml")
        for loc in soup.find_all("loc"):
            url = loc.text.strip()
            if "/insights/articles/" in url or "/insights/goldman-sachs-research/" in url:
                slug = url.rstrip("/").split("/")[-1]
                title = slug.replace("-", " ").title()
                # Try to get lastmod from sitemap
                parent = loc.find_parent("url")
                date = ""
                if parent:
                    lastmod = parent.find("lastmod")
                    if lastmod and lastmod.text:
                        parsed = _parse_date(lastmod.text.strip()[:10])
                        if parsed:
                            date = parsed
                articles.append({
                    "source": "Goldman Sachs",
                    "title": title,
                    "url": url,
                    "snippet": "",
                    "date": date,
                })
    except Exception:
        pass
    return articles


# ── Morgan Stanley ───────────────────────────────────────────────────────────

def scrape_morgan_stanley() -> list[dict]:
    articles = []
    for url in BANKS["Morgan Stanley"]["urls"]:
        soup = _fetch(url)
        if not soup:
            continue
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "/insights/" not in href and "/ideas/" not in href:
                continue
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) < 3:
                continue
            title = _clean(a_tag.get_text())
            if not title or len(title) < 10:
                parent = a_tag.find_parent(["article", "div", "li", "section"])
                if parent:
                    heading = parent.find(["h1", "h2", "h3", "h4", "h5"])
                    if heading:
                        title = _clean(heading.get_text())
            if not title or len(title) < 10:
                slug = parts[-1]
                title = slug.replace("-", " ").title()
            full_url = urljoin(BANKS["Morgan Stanley"]["base"], href)
            snippet = ""
            date = _find_date_near_link(a_tag)
            parent = a_tag.find_parent(["article", "div", "li", "section"])
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    snippet = _clean(p_tag.get_text())[:200]
            articles.append({
                "source": "Morgan Stanley",
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "date": date or "",
            })

    # Always include sitemap articles for broader coverage
    articles.extend(_scrape_morgan_stanley_sitemap())

    return _dedupe(articles)


def _scrape_morgan_stanley_sitemap() -> list[dict]:
    articles = []
    try:
        r = requests.get(
            "https://www.morganstanley.com/sitemap.xml",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml-xml")
        for loc in soup.find_all("loc"):
            url = loc.text.strip()
            # Include articles under /ideas/, /articles/, /insights/articles/
            if not ("/ideas/" in url or "/articles/" in url
                    or "/insights/articles/" in url):
                continue
            # Exclude category/topic pages
            if "/insights/topics/" in url:
                continue
            slug = url.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").title()
            parent = loc.find_parent("url")
            date = ""
            if parent:
                lastmod = parent.find("lastmod")
                if lastmod and lastmod.text:
                    parsed = _parse_date(lastmod.text.strip()[:10])
                    if parsed:
                        date = parsed
            articles.append({
                "source": "Morgan Stanley",
                "title": title,
                "url": url,
                "snippet": "",
                "date": date,
            })
    except Exception:
        pass
    return articles


# ── Citi ─────────────────────────────────────────────────────────────────────

def scrape_citi() -> list[dict]:
    articles = []
    skip_suffixes = {"/insights", "/insights/", "/insights/all", "/insights/all/", "/FAQs"}
    for url in BANKS["Citi"]["urls"]:
        soup = _fetch(url)
        if not soup:
            continue
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "/global/insights/" not in href and "/insights/" not in href:
                continue
            clean_href = href.rstrip("/")
            if any(clean_href.endswith(s.rstrip("/")) for s in skip_suffixes):
                continue
            if "FAQs" in href:
                continue
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) < 3:
                continue
            # Citi renders titles via JS; link text is "Learn More"
            # Always derive title from slug
            slug = parts[-1]
            title = slug.replace("-", " ").title()
            full_url = urljoin(BANKS["Citi"]["base"], href)
            date = _find_date_near_link(a_tag) or ""
            snippet = ""
            parent = a_tag.find_parent(["article", "div", "li", "section"])
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    snippet = _clean(p_tag.get_text())[:200]
            articles.append({
                "source": "Citi",
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "date": date,
            })
    return _dedupe(articles)


# ── HSBC ─────────────────────────────────────────────────────────────────────

def scrape_hsbc() -> list[dict]:
    articles = []
    for url in BANKS["HSBC"]["urls"]:
        soup = _fetch(url)
        if not soup:
            continue
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            # Match article paths under /hsbc-views/, /insight/, or /news-and-views/
            if ("/hsbc-views/" not in href and "/insight/" not in href
                    and "/news-and-views/" not in href):
                continue
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) < 3:
                continue
            # Skip category/nav pages
            nav_endings = {"/hsbc-views", "/news-and-views", "/news-and-views/news",
                           "/news-and-views/views", "/news-and-views/future-of-finance"}
            if href.rstrip("/").endswith(tuple(nav_endings)) or "?" in href:
                continue
            # HSBC link text is "Read more"; derive title from slug
            slug = parts[-1]
            title = slug.replace("-", " ").title()
            full_url = urljoin(BANKS["HSBC"]["base"], href)
            date = _find_date_near_link(a_tag) or ""
            snippet = ""
            parent = a_tag.find_parent(["article", "div", "li", "section"])
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    snippet = _clean(p_tag.get_text())[:200]
            articles.append({
                "source": "HSBC",
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "date": date,
            })
    return _dedupe(articles)


# ── RBC ──────────────────────────────────────────────────────────────────────

def scrape_rbc() -> list[dict]:
    articles = []
    for url in BANKS["RBC"]["urls"]:
        soup = _fetch(url)
        if not soup:
            continue
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "/insights/" not in href:
                continue
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) < 4:
                continue
            # Skip nav links
            if href.rstrip("/").endswith("/insights") or href.rstrip("/").endswith("/insights.page"):
                continue
            # RBC links often contain nested spans; use slug for clean title
            slug = parts[-1]
            title = slug.replace("-", " ").title()
            full_url = urljoin(BANKS["RBC"]["base"], href)
            snippet = ""
            # RBC URLs contain date: /data/YYYY/MM/slug
            date = ""
            date_match = re.search(r"/(\d{4})/(\d{2})/", href)
            if date_match:
                date = f"{date_match.group(1)}-{date_match.group(2)}-01"
            if not date:
                date = _find_date_near_link(a_tag) or ""
            parent = a_tag.find_parent(["article", "div", "li", "section"])
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    snippet = _clean(p_tag.get_text())[:200]
            articles.append({
                "source": "RBC",
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "date": date,
            })
    return _dedupe(articles)


# ── ZeroHedge ───────────────────────────────────────────────────────────────

_ZH_CATEGORIES = re.compile(
    r"^/(markets|economics|commodities|political|geopolitical|energy)/"
)


def scrape_zerohedge() -> list[dict]:
    articles = []
    for url in BANKS["ZeroHedge"]["urls"]:
        soup = _fetch(url)
        if not soup:
            continue
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if not _ZH_CATEGORIES.search(href):
                continue
            parts = [p for p in href.strip("/").split("/") if p]
            # Must have category + slug (at least 2 parts)
            if len(parts) < 2:
                continue
            # Skip category index links (e.g., /markets with no slug)
            slug = parts[-1]
            if slug in ("markets", "economics", "commodities", "political",
                         "geopolitical", "energy"):
                continue
            title = _clean(a_tag.get_text())
            if not title or len(title) < 10:
                title = slug.replace("-", " ").title()
            full_url = urljoin(BANKS["ZeroHedge"]["base"], href)
            date = ""
            snippet = ""
            # Look for snippet in parent container
            parent = a_tag.find_parent(["article", "div", "li", "section"])
            if parent:
                em_tag = parent.find("em")
                if em_tag:
                    snippet = _clean(em_tag.get_text())[:200]
                if not snippet:
                    p_tag = parent.find("p")
                    if p_tag:
                        snippet = _clean(p_tag.get_text())[:200]
            articles.append({
                "source": "ZeroHedge",
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "date": date,
            })
    return _dedupe(articles)


# ── Search engine ────────────────────────────────────────────────────────────

def _query_matches(q: str, article: dict) -> bool:
    """Check if query matches title, snippet, or the article slug.
    For short queries (<=3 chars), use word-boundary matching to avoid
    'ai' matching inside 'sustainability'."""
    if len(q) <= 3:
        pattern = r'\b' + re.escape(q) + r'\b'
        if re.search(pattern, article["title"], re.IGNORECASE):
            return True
        if re.search(pattern, article["snippet"], re.IGNORECASE):
            return True
        path = urlparse(article["url"]).path.rstrip("/")
        slug = path.split("/")[-1].lower() if "/" in path else ""
        # For slugs, word boundaries are hyphens
        slug_words = slug.split("-")
        return q in slug_words
    if q in article["title"].lower() or q in article["snippet"].lower():
        return True
    path = urlparse(article["url"]).path.rstrip("/")
    slug = path.split("/")[-1].lower() if "/" in path else ""
    return q in slug


def search_articles(query: str) -> dict:
    """Scrape all banks in parallel, filter by query, enrich dates, sort by date."""
    all_matched = []
    errors = []
    scrapers = {
        "JPMorgan": scrape_jpmorgan,
        "Goldman Sachs": scrape_goldman,
        "Morgan Stanley": scrape_morgan_stanley,
        "Citi": scrape_citi,
        "HSBC": scrape_hsbc,
        "RBC": scrape_rbc,
        "ZeroHedge": scrape_zerohedge,
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn): name for name, fn in scrapers.items()}
        for future in as_completed(futures):
            bank = futures[future]
            try:
                articles = future.result()
                q = query.lower()
                matched = [a for a in articles if _query_matches(q, a)]
                all_matched.extend(matched)
            except Exception as e:
                errors.append(f"{bank}: {e}")

    # Enrich missing dates by fetching article pages (up to 12 fetches)
    all_matched = _enrich_dates(all_matched, max_fetches=12)

    # Sort: articles with dates first (newest first), then undated
    def sort_key(a):
        d = a.get("date", "")
        if d:
            return (0, d)
        return (1, "")

    all_matched.sort(key=sort_key, reverse=True)
    # Fix: dated articles should be newest-first (already handled by reverse)
    # but undated (1, "") reversed puts them at front — fix by custom sort
    dated = [a for a in all_matched if a.get("date")]
    undated = [a for a in all_matched if not a.get("date")]
    dated.sort(key=lambda a: a["date"], reverse=True)
    final = dated + undated

    return {"articles": final, "total": len(final), "errors": errors}


# ── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>News Terminal</title>
<style>
  :root {
    --bg: #0a0e17;
    --card: #111827;
    --border: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #3b82f6;
    --accent-hover: #2563eb;
    --jp: #003A70;
    --gs: #4A6FA5;
    --ms: #002F6C;
    --citi: #003B70;
    --hsbc: #DB0011;
    --rbc: #003DA5;
    --zh: #F57C00;
    --success: #10b981;
    --error: #ef4444;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }

  .header {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    border-bottom: 1px solid var(--border);
    padding: 24px 0;
  }
  .header-inner {
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 24px;
  }
  .header h1 {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.5px;
    margin-bottom: 4px;
  }
  .header h1 span { color: var(--accent); }
  .header p { color: var(--muted); font-size: 13px; }
  .bank-pills {
    display: flex;
    gap: 8px;
    margin-top: 12px;
    flex-wrap: wrap;
  }
  .pill-link {
    text-decoration: none;
  }
  .pill {
    font-size: 11px;
    padding: 5px 14px;
    border-radius: 20px;
    font-weight: 600;
    color: #fff;
    letter-spacing: 0.3px;
    min-width: 60px;
    text-align: center;
    display: inline-block;
    transition: opacity 0.2s;
  }
  .pill-link:hover .pill { opacity: 0.85; }
  .pill.jp   { background: var(--jp); }
  .pill.gs   { background: var(--gs); }
  .pill.ms   { background: var(--ms); }
  .pill.citi { background: var(--citi); }
  .pill.hsbc { background: var(--hsbc); }
  .pill.rbc  { background: var(--rbc); }
  .pill.zh   { background: var(--zh); }

  .search-section {
    max-width: 1100px;
    margin: 28px auto 0;
    padding: 0 24px;
  }
  .search-box {
    display: flex;
    gap: 12px;
    align-items: stretch;
  }
  .search-input {
    flex: 1;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 20px;
    color: var(--text);
    font-size: 15px;
    outline: none;
    transition: border-color 0.2s;
  }
  .search-input:focus { border-color: var(--accent); }
  .search-input::placeholder { color: var(--muted); }
  .search-btn {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 10px;
    padding: 0 28px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
    white-space: nowrap;
  }
  .search-btn:hover { background: var(--accent-hover); }
  .search-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .status-bar {
    max-width: 1100px;
    margin: 16px auto 0;
    padding: 0 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    min-height: 28px;
  }
  .status-text { font-size: 13px; color: var(--muted); }
  .status-text.loading { color: var(--accent); }
  .status-text.error { color: var(--error); }
  .total-badge {
    background: var(--accent);
    color: #fff;
    font-size: 12px;
    font-weight: 700;
    padding: 3px 12px;
    border-radius: 20px;
  }

  .spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Ranked list ────────────────────────── */
  .results {
    max-width: 1100px;
    margin: 20px auto 60px;
    padding: 0 24px;
  }

  .list-header {
    display: grid;
    grid-template-columns: 42px 1fr 140px 150px;
    gap: 12px;
    padding: 10px 16px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }

  .article-row {
    display: grid;
    grid-template-columns: 42px 1fr 140px 150px;
    gap: 12px;
    align-items: center;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    text-decoration: none;
    color: var(--text);
    transition: background 0.15s;
  }
  .article-row:hover {
    background: rgba(59, 130, 246, 0.06);
  }

  .row-rank {
    font-size: 15px;
    font-weight: 700;
    color: var(--muted);
    text-align: center;
  }

  .row-info { min-width: 0; }
  .row-title {
    font-size: 14px;
    font-weight: 600;
    line-height: 1.4;
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .row-snippet {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.4;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .row-source {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .source-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .source-label {
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }

  .row-date {
    font-size: 13px;
    color: var(--muted);
    text-align: right;
    white-space: nowrap;
  }
  .row-date .date-value {
    color: var(--text);
    font-weight: 500;
  }
  .row-date .date-unknown {
    color: var(--muted);
    font-style: italic;
    font-size: 11px;
  }

  .empty-state {
    text-align: center;
    padding: 80px 24px;
    color: var(--muted);
  }
  .empty-state .icon { font-size: 48px; margin-bottom: 16px; }
  .empty-state h2 { font-size: 18px; margin-bottom: 8px; color: var(--text); }
  .empty-state p { font-size: 13px; max-width: 400px; margin: 0 auto; }

  @media (max-width: 500px) {
    .list-header,
    .article-row {
      grid-template-columns: 32px 1fr;
    }
    .list-header > :nth-child(3),
    .article-row > :nth-child(3),
    .list-header > :nth-child(4),
    .article-row > :nth-child(4) {
      display: none;
    }
    .search-box { flex-direction: column; }
    .search-btn { padding: 14px; }
  }
  @media (min-width: 501px) and (max-width: 800px) {
    .list-header,
    .article-row {
      grid-template-columns: 36px 1fr 110px 120px;
      gap: 8px;
    }
    .source-label { font-size: 11px; }
    .row-title { font-size: 13px; }
  }
</style>
</head>
<body>

<div class="header">
  <div class="header-inner">
    <h1>News <span>Terminal</span></h1>
    <p>Real-time article search across top investment banks</p>
    <div class="bank-pills">
      <a class="pill-link" href="https://www.jpmorgan.com/insights" target="_blank" rel="noopener"><span class="pill jp">JPMorgan</span></a>
      <a class="pill-link" href="https://www.goldmansachs.com/insights/articles" target="_blank" rel="noopener"><span class="pill gs">Goldman Sachs</span></a>
      <a class="pill-link" href="https://www.morganstanley.com/ideas" target="_blank" rel="noopener"><span class="pill ms">Morgan Stanley</span></a>
      <a class="pill-link" href="https://www.citigroup.com/global/insights" target="_blank" rel="noopener"><span class="pill citi">Citi</span></a>
      <a class="pill-link" href="https://www.hsbc.com/insight" target="_blank" rel="noopener"><span class="pill hsbc">HSBC</span></a>
      <a class="pill-link" href="https://www.rbccm.com/en/insights.page" target="_blank" rel="noopener"><span class="pill rbc">RBC</span></a>
      <a class="pill-link" href="https://www.zerohedge.com/markets" target="_blank" rel="noopener"><span class="pill zh">ZeroHedge</span></a>
    </div>
  </div>
</div>

<div class="search-section">
  <form class="search-box" id="searchForm">
    <input class="search-input" type="text" id="query"
      placeholder="Search articles… e.g. gold, AI, tariffs, recession" autofocus>
    <button class="search-btn" type="submit" id="searchBtn">Search</button>
  </form>
</div>

<div class="status-bar">
  <span class="status-text" id="status"></span>
  <span id="totalBadge"></span>
</div>

<div class="results" id="results">
  <div class="empty-state" id="emptyState">
    <div class="icon">&#x1F50D;</div>
    <h2>Search News Terminal</h2>
    <p>Enter a topic to scrape articles from 7 sources.
       Results are ranked by publish date.</p>
  </div>
</div>

<script>
const form = document.getElementById("searchForm");
const input = document.getElementById("query");
const btn = document.getElementById("searchBtn");
const statusEl = document.getElementById("status");
const totalBadge = document.getElementById("totalBadge");
const results = document.getElementById("results");

const bankMeta = {
  "JPMorgan":       { css: "jp", color: "#003A70" },
  "Goldman Sachs":  { css: "gs", color: "#4A6FA5" },
  "Morgan Stanley": { css: "ms", color: "#002F6C" },
  "Citi":           { css: "citi", color: "#003B70" },
  "HSBC":           { css: "hsbc", color: "#DB0011" },
  "RBC":            { css: "rbc", color: "#003DA5" },
  "ZeroHedge":      { css: "zh", color: "#F57C00" },
};

function formatDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  } catch { return iso; }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) return;

  btn.disabled = true;
  statusEl.className = "status-text loading";
  statusEl.innerHTML = '<span class="spinner"></span>Scraping articles across 7 sources…';
  totalBadge.innerHTML = "";
  results.innerHTML = "";

  try {
    const res = await fetch("/api/search?q=" + encodeURIComponent(q));
    const data = await res.json();

    if (data.total === 0) {
      statusEl.className = "status-text";
      statusEl.textContent = 'No articles found for "' + q + '"';
      results.innerHTML = `
        <div class="empty-state">
          <div class="icon">&#x1F4ED;</div>
          <h2>No results</h2>
          <p>Try a broader search term like "markets", "AI", or "economy"</p>
        </div>`;
      btn.disabled = false;
      return;
    }

    statusEl.className = "status-text";
    statusEl.textContent = 'Results for "' + q + '" — sorted by date (newest first)';
    totalBadge.innerHTML = '<span class="total-badge">' + data.total + " articles</span>";

    let html = `
      <div class="list-header">
        <div>#</div>
        <div>Article</div>
        <div>Source</div>
        <div style="text-align:right">Published</div>
      </div>`;

    data.articles.forEach((a, i) => {
      const meta = bankMeta[a.source] || { css: "jp", color: "#666" };
      const dateStr = a.date ? formatDate(a.date) : "";
      const dateHtml = dateStr
        ? '<span class="date-value">' + esc(dateStr) + "</span>"
        : '<span class="date-unknown">—</span>';
      const snippet = a.snippet ? esc(a.snippet) : "";

      html += `
        <a class="article-row" href="${esc(a.url)}" target="_blank" rel="noopener">
          <div class="row-rank">${i + 1}</div>
          <div class="row-info">
            <div class="row-title">${esc(a.title)}</div>
            ${snippet ? '<div class="row-snippet">' + snippet + "</div>" : ""}
          </div>
          <div class="row-source">
            <span class="source-dot" style="background:${meta.color}"></span>
            <span class="source-label">${esc(a.source)}</span>
          </div>
          <div class="row-date">${dateHtml}</div>
        </a>`;
    });

    if (data.errors && data.errors.length) {
      html += '<p class="status-text error" style="margin-top:16px;padding:0 16px">Errors: '
        + esc(data.errors.join("; ")) + "</p>";
    }

    results.innerHTML = html;
  } catch (err) {
    statusEl.className = "status-text error";
    statusEl.textContent = "Error: " + err.message;
  }
  btn.disabled = false;
});

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, content_type="text/html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"articles": [], "total": 0, "errors": ["No query provided"]})
    data = search_articles(q)
    return jsonify(data)


def open_browser():
    webbrowser.open("http://127.0.0.1:5050")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    print("\n  News Terminal")
    print("  Dashboard -> http://127.0.0.1:5050\n")
    app.run(host="127.0.0.1", port=5050, debug=False)
