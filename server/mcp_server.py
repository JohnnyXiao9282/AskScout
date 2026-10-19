"""MCP server with web tools for Agent mode.

Tools:
  search_web     Google search via SerpAPI
  fetch_url      read a web page as clean text
  find_in_page   find passages on a page that mention given keywords
  extract_links  list the links on a page
  check_url      status / redirects / response time / HTTPS for a URL

Spawned as a child process by agent.py over stdio.

Safety: only http(s) URLs to public IP addresses are fetched (blocks localhost,
private networks and cloud metadata endpoints), every redirect hop is re-checked,
and response size / time are capped.
"""
import ipaddress
import json
import os
import re
import socket
import sys
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from serpapi import GoogleSearch

SERPAPI_KEY = os.getenv("SERPAPI_KEY")

MAX_BYTES = 3_000_000  # never download more than ~3 MB
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
MAX_REDIRECTS = 5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AskScoutAgent/1.0"
)

mcp = FastMCP("askscout-web-tools")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class UnsafeURL(ValueError):
    pass


def _check_safe(url: str) -> str:
    """Reject anything that isn't a public http(s) URL."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url  # allow "example.com"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURL(f"Only http(s) URLs are allowed: {url}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or None)
    except socket.gaierror as e:
        raise UnsafeURL(f"Could not resolve host {parsed.hostname}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeURL(f"Refusing to access non-public address {ip} ({parsed.hostname}).")
    return url


def _get(url: str) -> tuple[httpx.Response, bytes, list[dict], float]:
    """GET with manual, re-validated redirects and a size cap.

    Returns (response, body, redirect_chain, elapsed_ms).
    """
    chain: list[dict] = []
    start = time.perf_counter()
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        current = _check_safe(url)
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current, follow_redirects=False) as resp:
                if resp.is_redirect and "location" in resp.headers:
                    nxt = urljoin(current, resp.headers["location"])
                    chain.append({"from": current, "to": nxt, "status": resp.status_code})
                    current = _check_safe(nxt)
                    continue
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                elapsed = (time.perf_counter() - start) * 1000
                return resp, bytes(body[:MAX_BYTES]), chain, elapsed
    raise UnsafeURL(f"Too many redirects (>{MAX_REDIRECTS}).")


def _soup(resp: httpx.Response, body: bytes) -> BeautifulSoup:
    encoding = resp.encoding or "utf-8"
    return BeautifulSoup(body.decode(encoding, errors="replace"), "html.parser")


def _readable_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "aside"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "pre", "blockquote"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            text = "#" * int(el.name[1]) + " " + text
        elif el.name == "li":
            text = "- " + text
        lines.append(text)
    if not lines:  # pages without semantic markup
        lines = [" ".join(root.get_text(" ", strip=True).split())]
    # drop consecutive duplicates (nested elements)
    out = [l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]]
    return "\n".join(out)


def _error(e: Exception) -> str:
    return json.dumps({"error": f"{type(e).__name__}: {e}"})


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def search_web(query: str, num: int = 5) -> str:
    """Search the web (Google via SerpAPI). Returns the top results with title,
    link and snippet, plus a direct answer box when Google has one.

    Args:
        query: The search query
        num: Number of results to return (1-10, default 5)
    """
    try:
        data = GoogleSearch(
            {"engine": "google", "q": query, "num": max(1, min(num, 10)), "api_key": SERPAPI_KEY}
        ).get_dict()
        if data.get("error"):
            return json.dumps({"error": data["error"]})
        results = [
            {"title": r.get("title"), "link": r.get("link"), "snippet": r.get("snippet", "")}
            for r in (data.get("organic_results") or [])[:num]
        ]
        out: dict = {"query": query, "results": results}
        box = data.get("answer_box") or {}
        answer = box.get("answer") or box.get("snippet")
        if answer:
            out["answer_box"] = answer
        return json.dumps(out)
    except Exception as e:  # noqa: BLE001
        return _error(e)


@mcp.tool()
def fetch_url(url: str, max_chars: int = 12000) -> str:
    """Open a web page and return its readable text content (headings, paragraphs,
    lists, tables) along with the page title and final URL after redirects.
    Use this to read and check what a page actually says.

    Args:
        url: The page URL (http or https)
        max_chars: Maximum characters of text to return (default 12000)
    """
    try:
        resp, body, chain, elapsed = _get(url)
        ctype = resp.headers.get("content-type", "")
        result: dict = {
            "url": str(resp.url),
            "status": resp.status_code,
            "content_type": ctype,
            "redirects": len(chain),
        }
        if "html" in ctype or not ctype:
            soup = _soup(resp, body)
            result["title"] = soup.title.get_text(strip=True) if soup.title else ""
            desc = soup.find("meta", attrs={"name": "description"})
            if desc and desc.get("content"):
                result["description"] = desc["content"]
            text = _readable_text(soup)
        elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
            text = body.decode(resp.encoding or "utf-8", errors="replace")
        else:
            text = f"[Non-text content ({ctype}), {len(body)} bytes - not readable]"
        max_chars = max(1000, min(max_chars, 30000))
        result["truncated"] = len(text) > max_chars
        result["text"] = text[:max_chars]
        return json.dumps(result)
    except Exception as e:  # noqa: BLE001
        return _error(e)


@mcp.tool()
def find_in_page(url: str, keywords: list[str], max_matches: int = 15) -> str:
    """Search a web page for passages mentioning any of the keywords
    (case-insensitive). Useful to verify whether a page mentions something,
    or to find specific facts (prices, dates, names) on long pages.

    Args:
        url: The page URL
        keywords: Words or phrases to look for
        max_matches: Maximum passages to return (default 15)
    """
    try:
        resp, body, _, _ = _get(url)
        text = _readable_text(_soup(resp, body))
        terms = [k.lower() for k in keywords if k.strip()]
        matches = []
        for line in text.split("\n"):
            low = line.lower()
            hit = [k for k in terms if k in low]
            if hit:
                matches.append({"keywords": hit, "text": line[:500]})
                if len(matches) >= max_matches:
                    break
        found = {k: any(k in m["keywords"] for m in matches) for k in terms}
        return json.dumps({"url": str(resp.url), "found": found, "matches": matches})
    except Exception as e:  # noqa: BLE001
        return _error(e)


@mcp.tool()
def extract_links(url: str, same_domain_only: bool = False, limit: int = 60) -> str:
    """List the links on a web page (anchor text + absolute URL), so you can
    decide which pages to open next.

    Args:
        url: The page URL
        same_domain_only: Only return links on the same domain (default false)
        limit: Maximum number of links (default 60)
    """
    try:
        resp, body, _, _ = _get(url)
        base = str(resp.url)
        host = urlparse(base).hostname
        seen, links = set(), []
        for a in _soup(resp, body).find_all("a", href=True):
            href = urljoin(base, a["href"]).split("#")[0]
            if not href.startswith(("http://", "https://")) or href in seen:
                continue
            if same_domain_only and urlparse(href).hostname != host:
                continue
            seen.add(href)
            links.append({"text": " ".join(a.get_text(" ", strip=True).split())[:120], "url": href})
            if len(links) >= max(1, min(limit, 200)):
                break
        return json.dumps({"url": base, "count": len(links), "links": links})
    except Exception as e:  # noqa: BLE001
        return _error(e)


@mcp.tool()
def check_url(url: str) -> str:
    """Check whether a URL is reachable: HTTP status, redirect chain, final URL,
    response time, content type/size, and whether it is served over HTTPS.

    Args:
        url: The URL to check
    """
    try:
        resp, body, chain, elapsed = _get(url)
        final = str(resp.url)
        return json.dumps(
            {
                "url": url,
                "final_url": final,
                "status": resp.status_code,
                "ok": resp.is_success,
                "reason": resp.reason_phrase,
                "redirects": chain,
                "response_time_ms": round(elapsed),
                "content_type": resp.headers.get("content-type", ""),
                "bytes": len(body),
                "https": final.startswith("https://"),
                "server": resp.headers.get("server", ""),
            }
        )
    except Exception as e:  # noqa: BLE001
        return _error(e)


if __name__ == "__main__":
    # Log to stderr; stdout is reserved for the MCP protocol.
    print("AskScout MCP web tools running on stdio.", file=sys.stderr)
    mcp.run(transport="stdio")
