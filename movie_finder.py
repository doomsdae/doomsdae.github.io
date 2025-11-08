# movie_finder.py
# Finds new movies to stream, ranks & groups by genre, writes CSV + HTML.
# Dark theme (toggleable), sticky IMDb search bar, back-to-top button.
# Auto-open: off by default (good for Task Scheduler). Use --open / --no-open.

import os
import time
import csv
import html
import webbrowser
import sys
import re as regex  # Rename to avoid conflict with requests.exceptions
from datetime import date, timedelta
from typing import Dict, List, Set, Union, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs) -> bool:
        print("Warning: python-dotenv not installed. Environment variables must be set manually.")
        return False

import requests

# ---------- Work from script's folder ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# ---------- Config validation ----------
def validate_int_env(name: str, default: int, min_val: int = 0) -> int:
    """Validate and convert integer environment variables"""
    try:
        value = int(os.getenv(name, str(default)))
        if value < min_val:
            print(f"Warning: {name}={value} is below minimum {min_val}, using default {default}")
            return default
        return value
    except ValueError:
        print(f"Warning: Invalid {name} value, using default {default}")
        return default

# ---------- Load environment variables ----------
load_dotenv()

API_KEY = os.getenv("TMDB_API_KEY")
if not API_KEY:
    raise SystemExit("Missing TMDB_API_KEY in .env")

COUNTRY = os.getenv("COUNTRY", "US").strip()
if not COUNTRY:
    print("Warning: Empty COUNTRY value, using default 'US'")
    COUNTRY = "US"

PREFERRED_PROVIDER_NAMES = {p.strip() for p in os.getenv("PROVIDERS", "").split(",") if p.strip()}

NEW_WINDOW_DAYS = validate_int_env("NEW_WINDOW_DAYS", 90, min_val=1)
MAX_RESULTS_PER_GENRE = validate_int_env("MAX_RESULTS_PER_GENRE", 0, min_val=0)  # 0 = no cap

# ---- Auto-open logic (default OFF) ----
AUTO_OPEN_ENV = os.getenv("AUTO_OPEN", "0").strip()  # default "0" for scheduler
if "--open" in sys.argv:
    AUTO_OPEN = True
elif "--no-open" in sys.argv:
    AUTO_OPEN = False
else:
    AUTO_OPEN = (AUTO_OPEN_ENV != "0")

BASE = "https://api.themoviedb.org/3"
PARAM = {"api_key": API_KEY}

OUT_CSV = os.path.join(BASE_DIR, "new_streaming_movies.csv")
OUT_HTML = os.path.join(BASE_DIR, "new_streaming_movies.html")
FAVICON_PATH = os.path.join(BASE_DIR, "favicon.ico")

# ---------- HTTP helper ----------
def _get(url: str, params: Optional[Dict] = None, retries: int = 3) -> Union[Dict, List]:
    """
    Make a GET request to the TMDb API with exponential backoff retry.
    
    Args:
        url: The API endpoint URL
        params: Optional query parameters
        retries: Number of retry attempts for failed requests
        
    Returns:
        Dict or List containing the JSON response
        
    Raises:
        SystemExit: If authentication fails
        requests.exceptions.RequestException: For other request failures
    """
    last_exception = None
    for attempt in range(retries):
        try:
            # Add rate limiting delay that increases with each retry
            if attempt > 0:
                delay = min(1.5 * (2 ** attempt), 10)  # Max 10 second delay
                time.sleep(delay)
            
            r = requests.get(url, params=(params or PARAM), timeout=20)
            
            if r.status_code == 429:  # Too Many Requests
                # Get retry-after header or use exponential backoff
                retry_after = int(r.headers.get('Retry-After', 1.5 * (2 ** attempt)))
                time.sleep(retry_after)
                continue
                
            if r.status_code == 401:
                raise SystemExit("TMDb authentication failed (401). Check TMDB_API_KEY in .env.")
                
            r.raise_for_status()
            
            data = r.json()
            if not isinstance(data, (dict, list)):
                raise ValueError(f"Expected dict or list response, got {type(data)}")
                
            return data
            
        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt == retries - 1:
                raise SystemExit(f"Failed to reach TMDb API after {retries} attempts: {e}")
            continue
            
        except (ValueError, TypeError) as e:
            raise SystemExit(f"Invalid response from TMDb API: {e}")
            
    if last_exception:
        raise last_exception
    raise SystemExit("Failed to reach TMDb API after all retries")

# ---------- Provider name normalization ----------
def _norm(s: str) -> str:
    """Lowercase and strip non-alphanumerics so 'Scream Box' == 'screambox'."""
    return regex.sub(r'[^a-z0-9]', '', (s or '').lower())

# ---------- TMDb helpers ----------
def fetch_provider_id_map(country: str = "US") -> tuple[dict[str, int], dict[int, str]]:
    """
    Fetch streaming provider information from TMDb.
    
    Returns:
        Tuple of (provider_name_to_id_map, provider_id_to_name_map)
    """
    data = _get(f"{BASE}/watch/providers/movie", {"watch_region": country, **PARAM})
    if not isinstance(data, dict):
        raise ValueError("Invalid provider data response")
        
    id_map: dict[str, int] = {}
    name_map: dict[int, str] = {}
    
    for p in data.get("results", []):
        if not isinstance(p, dict):
            continue
            
        name = str(p.get("provider_name", "")).strip()
        pid = p.get("provider_id")
        
        if name and isinstance(pid, int):
            id_map[name.lower()] = pid
            name_map[pid] = name
            
    return id_map, name_map

def fetch_genre_map() -> dict[int, str]:
    """Fetch movie genre mapping from TMDb."""
    data = _get(f"{BASE}/genre/movie/list", PARAM)
    if not isinstance(data, dict):
        raise ValueError("Invalid genre data response")
        
    return {
        g["id"]: g["name"] 
        for g in data.get("genres", [])
        if isinstance(g, dict) and "id" in g and "name" in g
        and isinstance(g["id"], int) and isinstance(g["name"], str)
    }

def resolve_preferred_ids(names, id_map):
    """
    Resolve preferred provider names (from .env) to provider IDs.
    Matching ignores case, spaces, and punctuation.
    If names is empty, allow ALL providers.
    """
    if not names:
        return set(id_map.values())

    norm_to_pid = { _norm(k): v for k, v in id_map.items() }
    wanted = {_norm(x) for x in names}
    matched = {norm_to_pid[n] for n in wanted if n in norm_to_pid}
    return matched

def discover_new_movies(country: str = "US", since_days: int = 90, pages: int = 5) -> list[dict]:
    """
    Discover new movies released in the specified time window.
    
    Args:
        country: Two-letter country code
        since_days: Number of days to look back
        pages: Maximum number of pages to fetch
        
    Returns:
        List of movie data dictionaries
    """
    since = (date.today() - timedelta(days=since_days)).isoformat()
    results = []
    
    for page in range(1, pages + 1):
        params = {
            "sort_by": "popularity.desc",
            "include_adult": "false",
            "include_video": "false",
            "region": country,
            "with_release_type": "2|3|4|5",  # Theatrical, physical, digital, TV
            "primary_release_date.gte": since,
            "page": page,
            **PARAM
        }
        
        try:
            data = _get(f"{BASE}/discover/movie", params)
            if not isinstance(data, dict):
                print(f"Warning: Invalid response format on page {page}")
                continue
                
            page_results = data.get("results", [])
            if not isinstance(page_results, list):
                print(f"Warning: Invalid results format on page {page}")
                continue
                
            results.extend(page_results)
            
            # Stop if we've reached the last page
            total_pages = data.get("total_pages", 1)
            if not isinstance(total_pages, int):
                print("Warning: Invalid total_pages format")
                break
                
            if page >= total_pages:
                break
                
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            continue
            
    return results

def get_watch_providers(movie_id: Union[int, str]) -> dict:
    """
    Get streaming provider information for a movie.
    
    Args:
        movie_id: TMDb movie ID
        
    Returns:
        Dictionary of provider information for the current country
    """
    try:
        data = _get(f"{BASE}/movie/{movie_id}/watch/providers")
        if not isinstance(data, dict):
            return {}
            
        results = data.get("results", {})
        if not isinstance(results, dict):
            return {}
            
        country_data = results.get(COUNTRY, {})
        if not isinstance(country_data, dict):
            return {}
            
        return country_data
        
    except Exception as e:
        print(f"Error fetching providers for movie {movie_id}: {e}")
        return {}

def get_imdb_id(movie_id: Union[int, str]) -> Optional[str]:
    """
    Get IMDb ID for a movie.
    
    Args:
        movie_id: TMDb movie ID
        
    Returns:
        IMDb ID if available, None otherwise
    """
    try:
        data = _get(f"{BASE}/movie/{movie_id}/external_ids")
        if not isinstance(data, dict):
            return None
            
        imdb_id = data.get("imdb_id")
        return str(imdb_id) if imdb_id else None
        
    except Exception as e:
        print(f"Error fetching IMDb ID for movie {movie_id}: {e}")
        return None

def pick_flatrate_providers(provider_section: dict, allowed_ids: Set[int]) -> list[str]:
    """
    Pick streaming providers from provider data that match allowed provider IDs.
    Checks both flatrate and ad-supported sections.
    
    Args:
        provider_section: Provider data from TMDb API
        allowed_ids: Set of allowed provider IDs
        
    Returns:
        Sorted list of unique provider names
    """
    picked = []
    
    for section in ("flatrate", "ads"):
        section_data = provider_section.get(section, [])
        if not isinstance(section_data, list):
            continue
            
        for item in section_data:
            if not isinstance(item, dict):
                continue
                
            provider_id = item.get("provider_id")
            if not isinstance(provider_id, int):
                continue
                
            if provider_id in allowed_ids:
                name = item.get("provider_name")
                if isinstance(name, str) and name.strip():
                    picked.append(name.strip())
                    
    return sorted(set(picked))

def rank_score(vote_average: Optional[float], vote_count: Optional[int], popularity: Optional[float]) -> float:
    """
    Calculate a weighted score for movie ranking using Bayesian average.
    
    Args:
        vote_average: TMDb vote average (0-10)
        vote_count: Number of votes
        popularity: TMDb popularity score
        
    Returns:
        Combined score between 0 and 10
    
    The formula uses:
    - Bayesian average for vote rating (weighted with global mean)
    - Popularity score normalized to 0-10 range
    Final score is 70% Bayesian rating + 30% normalized popularity
    """
    # Sanitize inputs
    v = max(0, vote_count if isinstance(vote_count, int) else 0)
    R = max(0.0, min(10.0, float(vote_average if vote_average is not None else 0.0)))
    P = max(0.0, float(popularity if popularity is not None else 0.0))
    
    # Bayesian average parameters
    m = 150  # Minimum votes to get full weight
    C = 6.5  # Global mean rating
    
    # Calculate Bayesian average
    bayes = (v/(v+m))*R + (m/(v+m))*C
    
    # Normalize popularity (typical range 0-1000) to 0-10
    norm_popularity = min(10.0, P/100.0)
    
    # Weighted combination
    return 0.7*bayes + 0.3*norm_popularity

# ---------- HTML: IMDb search header (function) ----------

def provider_filter_toolbar():
    return """
<div id="provider-filter" role="group" aria-label="Filter by streaming provider" style="display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0">
  <button class="btn pf" data-pf="All" aria-pressed="true">All</button>
  <button class="btn pf" data-pf="Netflix">Netflix</button>
  <button class="btn pf" data-pf="Hulu">Hulu</button>
  <button class="btn pf" data-pf="Amazon Prime Video">Prime Video</button>
  <button class="btn pf" data-pf="Max">Max</button>
  <button class="btn pf" data-pf="Disney+">Disney+</button>
</div>
"""
def imdb_search_header() -> str:
    """
    Returns a sticky IMDb search bar with:
      - title/person ID detection (tt... / nm...)
      - '/' keyboard shortcut to focus
      - opens results in a new tab
    """
    return """
<header class="imdb-header" role="banner" aria-label="IMDb search header">
  <div class="imdb-container">
    <div class="imdb-brand">
      <span>🎬 Movie Finder</span>
      <span class="dot">•</span>
      <span>IMDb Search</span>
    </div>
    <form id="imdb-form" class="imdb-form" role="search" aria-label="Search IMDb">
      <input
        id="imdb-query"
        type="search"
        placeholder="Search IMDb (title, person, or tt/nm ID)…"
        autocomplete="off"
        aria-label="Search IMDb"
      />
      <button type="submit" title="Search IMDb">Search</button>
    </form>
  </div>
</header>
"""

# ---------- Optional: list providers flag ----------
if "--list-providers" in sys.argv:
    id_map, name_map = fetch_provider_id_map(COUNTRY)
    print(f"Providers in {COUNTRY} per TMDb:")
    for name in sorted(id_map.keys()):
        print(" -", name)
    sys.exit(0)

# ---------- Main ----------
def main():
    id_map, id_to_name = fetch_provider_id_map(COUNTRY)
    allowed_ids = resolve_preferred_ids(PREFERRED_PROVIDER_NAMES, id_map)
    if not allowed_ids:
        print("No specific providers matched; showing all providers instead.")
        allowed_ids = set(id_to_name.keys())

    genre_map = fetch_genre_map()
    candidates = discover_new_movies(COUNTRY, NEW_WINDOW_DAYS, pages=5)

    movies = []
    for m in candidates:
        pid = m["id"]
        wp = get_watch_providers(pid)
        providers = pick_flatrate_providers(wp, allowed_ids)
        if not providers:
            continue

        imdb_id = get_imdb_id(pid)
        imdb_link = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else ""
        genre_ids = m.get("genre_ids") or []
        genres = [genre_map[g] for g in genre_ids if g in genre_map] or ["Uncategorized"]
        movies.append({
            "title": m.get("title") or "",
            "year": (m.get("release_date") or "")[:4],
            "rating": round(m.get("vote_average", 0.0), 1),
            "votes": int(m.get("vote_count", 0) or 0),
            "popularity": float(m.get("popularity", 0.0) or 0.0),
            "providers": ", ".join(providers),
            "imdb_link": imdb_link,
            "genres": genres
        })

    # Build per-genre buckets
    by_genre = {}
    for mv in movies:
        for g in mv["genres"]:
            by_genre.setdefault(g, []).append(mv)

    # Sort genres alphabetically
    genre_names_sorted = sorted(by_genre.keys(), key=lambda s: s.lower())

    # Sort each genre's movies by rank and optionally cap
    for g in genre_names_sorted:
        by_genre[g].sort(key=lambda x: rank_score(x["rating"], x["votes"], x["popularity"]), reverse=True)
        if MAX_RESULTS_PER_GENRE and MAX_RESULTS_PER_GENRE > 0:
            by_genre[g] = by_genre[g][:MAX_RESULTS_PER_GENRE]

    # ----- CSV (one row per (movie, genre)) -----
    fieldnames = ["genre", "title", "year", "rating", "votes", "providers", "imdb_link"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for g in genre_names_sorted:
            for mv in by_genre[g]:
                w.writerow({
                    "genre": g,
                    "title": mv["title"],
                    "year": mv["year"],
                    "rating": mv["rating"],
                    "votes": mv["votes"],
                    "providers": mv["providers"],
                    "imdb_link": mv["imdb_link"],
                })

    # ----- HTML build -----
    favicon_tag = '<link rel="icon" type="image/x-icon" href="favicon.ico">' if os.path.exists(FAVICON_PATH) else ""
    provider_label = ', '.join(sorted(PREFERRED_PROVIDER_NAMES)) if PREFERRED_PROVIDER_NAMES else 'All'
    generated_at = date.today().isoformat()

    toc_items = []
    for g in genre_names_sorted:
        anchor = g.lower().replace(" ", "-").replace("&", "and")
        toc_items.append(f"<li><a href='#{html.escape(anchor)}'>{html.escape(g)}</a></li>")
    toc_html = "<ul>" + "\n".join(toc_items) + "</ul>" if toc_items else "<p>No genres found.</p>"

    def row_html(mv):
        title = html.escape(mv["title"])
        title_link = f"<a href='{mv['imdb_link']}' target='_blank' rel='noopener'>{title}</a>" if mv["imdb_link"] else title
        return (
            f"<tr>"
            f"<td>{title_link}</td>"
            f"<td>{mv['year']}</td>"
            f"<td>{mv['rating']}</td>"
            f"<td>{mv['votes']}</td>"
            f"<td>{html.escape(mv['providers'])}</td>"
            f"</tr>"
        )

    sections = []
    for g in genre_names_sorted:
        anchor = g.lower().replace(" ", "-").replace("&", "and")
        rows = "\n".join(row_html(mv) for mv in by_genre[g]) or "<tr><td colspan='5'>No results.</td></tr>"
        section_html = f"""
<section id="{html.escape(anchor)}">
  <h2>{html.escape(g)}</h2>
  <table>
    <thead><tr><th>Title</th><th>Year</th><th>Rating</th><th>Votes</th><th>Where</th></tr></thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</section>
"""
        sections.append(section_html)

    # -- Styles & scripts (includes search behavior) --
    provider_filter_script = """
<script>
document.addEventListener('DOMContentLoaded', function() {
  const buttons = document.querySelectorAll('#provider-filter .pf');
  let active = 'All';
  function setActive(name) {
    active = name;
    buttons.forEach(b => b.setAttribute('aria-pressed', b.dataset.pf === active ? 'true' : 'false'));
    document.querySelectorAll('section table tbody tr').forEach(tr => {
      const providerCell = tr.querySelector('td:nth-child(5)');
      const matches = !providerCell ? true : (active === 'All' || (providerCell.textContent || '').includes(active));
      tr.style.display = matches ? '' : 'none';
    });
  }
  buttons.forEach(b => b.addEventListener('click', () => setActive(b.dataset.pf)));
  setActive('All');
});
</script>
"""
    styles = f"""
<style>
:root {{
  --bg: #0b0f14;
  --panel: #0f1720;
  --muted: #94a3b8;
  --text: #e5e7eb;
  --text-dim: #cbd5e1;
  --border: #1f2937;
  --accent: #60a5fa;
  --accent-hover: #93c5fd;
  --thead: #111827;
  --row-alt: #0d141d;
}}
:root[data-theme="light"] {{
  --bg: #f7fafc;
  --panel: #ffffff;
  --muted: #475569;
  --text: #0f172a;
  --text-dim: #334155;
  --border: #e2e8f0;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --thead: #f1f5f9;
  --row-alt: #f8fafc;
}}
* {{ box-sizing: border-box; }}
html, body {{ height: 100%; }}
body {{
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  margin: 24px;
  line-height: 1.5;
  color: var(--text);
  background: var(--bg);
  background-image:
    radial-gradient(1200px 800px at 20% -10%, rgba(16,24,38,0.6) 0%, transparent 60%),
    radial-gradient(1000px 600px at 120% 10%, rgba(15,22,34,0.5) 0%, transparent 60%);
  scroll-padding-top: 112px; /* sticky header + controls */
}}
h1 {{ margin: 0 0 8px; color: var(--text); }}
h2 {{
  margin: 28px 0 10px;
  color: var(--text);
  border-bottom: 1px solid var(--border);
  padding-bottom: 6px;
}}
.meta {{ color: var(--muted); margin-bottom: 16px; }}
nav.toc {{
  background: var(--panel);
  border: 1px solid var(--border);
  padding: 12px;
  border-radius: 10px;
  margin: 12px 0 16px;
}}
nav.toc ul {{ margin: 0; padding-left: 18px; }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ color: var(--accent-hover); text-decoration: underline; }}
table {{
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
  border: 1px solid var(--border);
  background: var(--panel);
  border-radius: 10px;
  overflow: hidden;
}}
th, td {{ border-bottom: 1px solid var(--border); padding: 10px 12px; }}
th {{
  background: var(--thead);
  text-align: left;
  color: var(--text-dim);
  font-weight: 600;
  letter-spacing: 0.2px;
}}
tbody tr:nth-child(even) {{ background: var(--row-alt); }}
tbody tr:hover {{ background: color-mix(in oklab, var(--accent) 18%, transparent); }}

/* IMDb header */
.imdb-header {{
  position: sticky;
  top: 0;
  z-index: 10000;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 0;
  margin-bottom: 10px;
}}
.imdb-container {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 16px;
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
}}
.imdb-brand {{ color: var(--text); font-weight: 600; display: flex; align-items: center; gap: 8px; }}
.imdb-brand .dot {{ color: var(--muted); }}
.imdb-form {{ display: flex; gap: 8px; flex: 1; justify-content: flex-end; }}
.imdb-form input {{
  width: min(520px, 100%);
  background: #161618;
  border: 1px solid var(--border);
  color: var(--text);
  padding: 10px 12px;
  border-radius: 10px;
  outline: none;
}}
:root[data-theme="light"] .imdb-form input {{ background: #f8fafc; }}
.imdb-form input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(96,165,250,0.18); }}
.imdb-form button {{
  background: var(--accent);
  color: #111;
  border: none;
  padding: 10px 14px;
  border-radius: 10px;
  font-weight: 700;
  cursor: pointer;
}}
.imdb-form button:hover {{ filter: brightness(0.95); }}

/* Floating controls */
#controls {{
  position: sticky;
  top: 62px; /* below IMDb header */
  display: flex;
  gap: 8px;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  z-index: 9999;
}}
.btn {{
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text);
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
  font: inherit;
}}
.btn:hover {{ border-color: var(--accent); }}
#backToTop {{
  position: fixed;
  right: 16px;
  bottom: 16px;
  padding: 10px 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text);
  display: none;
  cursor: pointer;
}}
#backToTop:hover {{ border-color: var(--accent); }}
</style>
"""

    scripts = """
<script>
(function() {
  const root = document.documentElement;
  const btn = document.getElementById('themeToggle');
  const back = document.getElementById('backToTop');

  // URL param override (?theme=light or ?theme=dark)
  const params = new URLSearchParams(window.location.search);
  const urlTheme = params.get('theme');
  const saved = localStorage.getItem('theme');
  let theme = (urlTheme === 'light' || urlTheme === 'dark') ? urlTheme
            : (saved === 'light' || saved === 'dark') ? saved
            : 'dark';

  function applyTheme(t) {
    root.setAttribute('data-theme', t);
    btn.textContent = (t === 'dark') ? '🌙 Dark' : '☀️ Light';
    let meta = document.querySelector('meta[name="color-scheme"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.setAttribute('name', 'color-scheme');
      document.head.appendChild(meta);
    }
    meta.setAttribute('content', t + ' light');
  }

  applyTheme(theme);

  btn.addEventListener('click', () => {
    theme = (theme === 'dark') ? 'light' : 'dark';
    localStorage.setItem('theme', theme);
    applyTheme(theme);
  });

  // Back to top
  window.addEventListener('scroll', () => {
    back.style.display = (window.scrollY > 200) ? 'block' : 'none';
  });
  back.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // IMDb search + "/" shortcut
  const form = document.getElementById('imdb-form');
  const input = document.getElementById('imdb-query');

  window.addEventListener('keydown', (e) => {
    const a = document.activeElement;
    const typing = a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable);
    if (!typing && e.key === '/') {
      e.preventDefault();
      input && input.focus();
    }
  });

  form && form.addEventListener('submit', (e) => {
    e.preventDefault();
    var q = (input && input.value ? input.value : '').trim();
    if (!q) return;

    // Direct IDs
    if (/^tt\\d{5,10}$/i.test(q)) {
      window.open('https://www.imdb.com/title/' + q + '/', '_blank', 'noopener');
      return;
    }
    if (/^nm\\d{5,10}$/i.test(q)) {
      window.open('https://www.imdb.com/name/' + q + '/', '_blank', 'noopener');
      return;
    }

    // Default: title search
    var url = 'https://www.imdb.com/find/?q=' + encodeURIComponent(q) + '&s=tt';
    window.open(url, '_blank', 'noopener');
  });
})();
</script>
"""

    style_button = """
<style>
.btn {
    display: inline-flex;
    align-items: center;
    gap: .5rem;
    padding: .5rem .75rem;
    border: 1px solid #333;
    border-radius: .5rem;
    background: #111;
    color: #ddd;
    text-decoration: none;
}
.btn:hover {
    background: #151515;
}
</style>
"""
    
    full_html = f"""<!doctype html>
<html data-theme="dark">
<head>
<meta charset="utf-8">
<title>New Movies (Streaming)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
{favicon_tag}
{styles}
{style_button}
</head>
<body>

{imdb_search_header()}

<div id="controls">
  <div><a href="new_streaming_movies.csv" class="btn" download>Download CSV</a></div>
  <div></div>
  <button id="themeToggle" class="btn" aria-label="Toggle theme">🌙 Dark</button>
</div>

<h1>New Movies - ({COUNTRY})</h1>
<div class="meta">Providers: {html.escape(provider_label)} · Window: last {NEW_WINDOW_DAYS} days · Generated: {generated_at}</div>

<nav class="toc">
  <strong>Jump to genre:</strong>
  {toc_html}
</nav>

{"".join(sections) if sections else "<p>No movies matched your filters.</p>"}

<button id="backToTop" title="Back to top">↑</button>
{scripts}
{provider_filter_script}
</body>
</html>"""

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(full_html)

    # Console summary
    total_rows = sum(len(v) for v in by_genre.values())
    print(f"Wrote {total_rows} rows grouped into {len(genre_names_sorted)} genres.")
    print(f"  - {OUT_CSV}")
    print(f"  - {OUT_HTML}")

    # Auto-open only if enabled (default OFF)
    if AUTO_OPEN:
        try:
            webbrowser.open(f"file:///{OUT_HTML.replace('\\', '/')}")
        except Exception as e:
            if os.name == "nt":
                try:
                    os.startfile(OUT_HTML)  # type: ignore[attr-defined]
                except Exception:
                    print(f"Could not auto-open HTML: {e}")
            else:
                print(f"Could not auto-open HTML: {e}")

if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.RequestException as re:
        print("Network/API error:", re)
        sys.exit(1)
