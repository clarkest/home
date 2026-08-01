#!/usr/bin/env python3
"""
libby_rank.py — find the highest-rated books available RIGHT NOW from a
library's OverDrive/Libby collection.

Pulls the library's digital catalog from OverDrive's public "Thunder" API
(no auth, no key), keeps only titles with a copy available this second,
looks up a community rating for each, and ranks with a Bayesian weighted
score so that a 4.7 from 12 raters doesn't beat a 4.3 from 90,000.

Stdlib only. No pip install, no venv.

Quick start
-----------
    # what genres does the collection expose?
    python3 libby_rank.py --facets

    # top sci-fi ebooks available now
    python3 libby_rank.py --subject "Science Fiction" --top 25

    # audiobooks, keyword search instead of subject
    python3 libby_rank.py --query "space opera" --media audiobook

    # widen the net, write a CSV
    python3 libby_rank.py --subject "Mystery" --pages 12 --csv mystery.csv
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

THUNDER = "https://thunder.api.overdrive.com/v2"
CLIENT_ID = "dewey"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CACHE_PATH = os.path.expanduser("~/.libby_rank_cache.db")


# ---------------------------------------------------------------- http

def get_json(url, params=None, headers=None, tries=3):
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        url = url + "?" + urllib.parse.urlencode(clean)
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {url} :: {last}")


# ---------------------------------------------------------------- cache

def cache_open():
    con = sqlite3.connect(CACHE_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS ratings "
                "(key TEXT PRIMARY KEY, payload TEXT, ts INTEGER)")
    con.commit()
    return con


def cache_get(con, key, max_age_days=30):
    row = con.execute("SELECT payload, ts FROM ratings WHERE key=?",
                      (key,)).fetchone()
    if not row:
        return None
    if time.time() - row[1] > max_age_days * 86400:
        return None
    return json.loads(row[0])


def cache_put(con, key, payload):
    con.execute("INSERT OR REPLACE INTO ratings VALUES (?,?,?)",
                (key, json.dumps(payload), int(time.time())))
    con.commit()


# ---------------------------------------------------------------- overdrive

def thunder_page(library, page, per_page, query=None, subject=None,
                 media=None, facets=False):
    """One page of the library's collection.

    We deliberately send a permissive param set. OverDrive ignores params it
    doesn't recognise, and we re-verify availability client-side anyway, so a
    wrong filter name degrades to 'fetched too much' rather than 'crashed'.
    """
    fmt = {"ebook": "ebook-overdrive,ebook-media-do,ebook-overdrive-provisional",
           "audiobook": "audiobook-overdrive,audiobook-overdrive-provisional",
           "any": None}[media or "any"]
    params = {
        "query": query,
        "subject": subject,
        "mediaType": None if media in (None, "any") else media,
        "format": fmt,
        "page": page,
        "perPage": per_page,
        "availability": "available",     # hint; verified locally regardless
        "showOnlyAvailable": "true",     # alternate spelling seen in the wild
        "includeFacets": "true" if facets else None,
        "truncateDescription": "true",
        "x-client-id": CLIENT_ID,
    }
    return get_json(f"{THUNDER}/libraries/{library}/media", params,
                    headers={"Referer": "https://libbyapp.com/",
                             "Origin": "https://libbyapp.com"})


def dig(d, *path, default=None):
    cur = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return default
    return default if cur is None else cur


def available_now(item):
    """True only if a copy is borrowable this second.

    Checks several field spellings because OverDrive's payload has drifted
    over the years and different endpoints populate different keys.
    """
    for key in ("isAvailable", "available"):
        v = item.get(key)
        if isinstance(v, bool):
            return v
    for key in ("availableCopies", "availableCopyCount"):
        v = item.get(key)
        if isinstance(v, (int, float)):
            return v > 0
    wait = item.get("estimatedWaitDays")
    if isinstance(wait, (int, float)):
        return wait <= 0
    return False


ISBN_RE = re.compile(r"^\d{9}[\dX]$|^\d{13}$")


def extract_isbn(item):
    cands = []
    for f in item.get("formats") or []:
        if f.get("isbn"):
            cands.append(str(f["isbn"]))
        for ident in f.get("identifiers") or []:
            if str(ident.get("type", "")).upper() == "ISBN":
                cands.append(str(ident.get("value", "")))
    cands = [c.replace("-", "").strip() for c in cands]
    cands = [c for c in cands if ISBN_RE.match(c)]
    cands.sort(key=lambda c: (len(c) != 13, c))   # prefer ISBN-13
    return cands[0] if cands else None


def authors(item):
    names = [c.get("name") for c in (item.get("creators") or [])
             if str(c.get("role", "")).lower() in ("author", "")]
    if not names:
        names = [c.get("name") for c in (item.get("creators") or [])]
    return ", ".join(n for n in names if n)[:60]


def parse_item(item, library):
    return {
        "id": item.get("id") or item.get("titleId"),
        "title": (item.get("title") or "").strip(),
        "author": authors(item),
        "isbn": extract_isbn(item),
        "media": dig(item, "type", "id", default="") or item.get("mediaType", ""),
        "subjects": ", ".join(s.get("name", "") for s in
                              (item.get("subjects") or []))[:80],
        "copies_avail": item.get("availableCopies"),
        "copies_owned": item.get("ownedCopies"),
        "holds": item.get("holdsCount"),
        "od_rating": dig(item, "starRating"),
        "od_rating_n": dig(item, "starRatingCount"),
        "url": f"https://{library}.overdrive.com/media/{item.get('id')}",
    }


def harvest(library, pages, per_page, **kw):
    seen, out = set(), []
    for page in range(1, pages + 1):
        data = thunder_page(library, page, per_page, **kw)
        items = (data or {}).get("items") or []
        if not items:
            break
        for it in items:
            if not available_now(it):
                continue
            rec = parse_item(it, library)
            if not rec["id"] or rec["id"] in seen:
                continue
            seen.add(rec["id"])
            out.append(rec)
        sys.stderr.write(f"\r  page {page}: {len(out)} available so far")
        sys.stderr.flush()
        time.sleep(0.5)          # be a good citizen
    sys.stderr.write("\n")
    return out


# ---------------------------------------------------------------- ratings

def rate_googlebooks(rec, con):
    key = "gb:" + (rec["isbn"] or f'{rec["title"]}|{rec["author"]}')
    hit = cache_get(con, key)
    if hit is not None:
        return hit
    if rec["isbn"]:
        q = f'isbn:{rec["isbn"]}'
    else:
        q = f'intitle:{rec["title"]}'
        if rec["author"]:
            q += f' inauthor:{rec["author"].split(",")[0]}'
    data = get_json("https://www.googleapis.com/books/v1/volumes",
                    {"q": q, "maxResults": 1,
                     "key": os.environ.get("GOOGLE_BOOKS_KEY")})
    vi = dig(data or {}, "items", default=None)
    out = {"rating": None, "n": 0}
    if isinstance(vi, list) and vi:
        info = vi[0].get("volumeInfo", {})
        if info.get("averageRating"):
            out = {"rating": float(info["averageRating"]),
                   "n": int(info.get("ratingsCount") or 0)}
    cache_put(con, key, out)
    time.sleep(0.35)
    return out


def rate_hardcover(rec, con):
    token = os.environ.get("HARDCOVER_TOKEN")
    if not token:
        raise SystemExit("HARDCOVER_TOKEN not set. Get a free token at "
                         "hardcover.app/account/api")
    key = "hc:" + (rec["isbn"] or f'{rec["title"]}|{rec["author"]}')
    hit = cache_get(con, key)
    if hit is not None:
        return hit
    if rec["isbn"]:
        where = f'editions: {{isbn_13: {{_eq: "{rec["isbn"]}"}}}}'
    else:
        safe = rec["title"].replace('"', "")
        where = f'title: {{_ilike: "{safe}"}}'
    gql = ('query { books(where: {%s}, limit: 1) '
           '{ title rating ratings_count } }' % where)
    body = json.dumps({"query": gql}).encode()
    req = urllib.request.Request(
        "https://api.hardcover.app/v1/graphql", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}", "User-Agent": UA})
    out = {"rating": None, "n": 0}
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        books = dig(data, "data", "books", default=[])
        if books:
            b = books[0]
            if b.get("rating"):
                out = {"rating": float(b["rating"]),
                       "n": int(b.get("ratings_count") or 0)}
    except Exception:  # noqa: BLE001
        pass
    cache_put(con, key, out)
    time.sleep(0.25)
    return out


def rate_overdrive(rec, con):
    """Free — OverDrive ships its own star rating in the search payload."""
    if rec.get("od_rating"):
        return {"rating": float(rec["od_rating"]),
                "n": int(rec.get("od_rating_n") or 0)}
    return {"rating": None, "n": 0}


PROVIDERS = {"googlebooks": rate_googlebooks,
             "hardcover": rate_hardcover,
             "overdrive": rate_overdrive}


# ---------------------------------------------------------------- ranking

def bayesian(records, prior_weight):
    """IMDb-style weighted rating.

        WR = (v/(v+m))*R + (m/(v+m))*C

    R = this book's average, v = its number of ratings, C = pool mean,
    m = how many ratings a book needs before we mostly trust its own score.
    Books with few ratings get pulled toward the pool mean.
    """
    rated = [r for r in records if r.get("rating")]
    if not rated:
        return records
    C = sum(r["rating"] for r in rated) / len(rated)
    m = prior_weight
    for r in records:
        R, v = r.get("rating"), r.get("n_ratings") or 0
        if R is None:
            r["score"] = None
        else:
            r["score"] = (v / (v + m)) * R + (m / (v + m)) * C
    return records


# ---------------------------------------------------------------- output

def show_table(rows, top):
    if not rows:
        print("\nNothing matched. Try --pages 10, a broader --subject, "
              "or --min-ratings 0.")
        return
    w = min(52, max(len(r["title"]) for r in rows[:top]))
    print(f"\n{'#':>3}  {'score':>5}  {'raw':>4}  {'ratings':>8}  "
          f"{'cps':>3}  {'title'.ljust(w)}  author")
    print("-" * (3 + 2 + 5 + 2 + 4 + 2 + 8 + 2 + 3 + 2 + w + 2 + 24))
    for i, r in enumerate(rows[:top], 1):
        title = r["title"][:w].ljust(w)
        score = f'{r["score"]:.2f}' if r.get("score") else "  — "
        raw = f'{r["rating"]:.1f}' if r.get("rating") else "  —"
        print(f'{i:>3}  {score:>5}  {raw:>4}  {r.get("n_ratings") or 0:>8}  '
              f'{r.get("copies_avail") or 0:>3}  {title}  {r["author"][:24]}')
    print(f"\n{len(rows)} rated titles available now. "
          f"Links are in the CSV if you wrote one.")


def write_csv(rows, path):
    cols = ["score", "rating", "n_ratings", "title", "author", "media",
            "copies_avail", "copies_owned", "holds", "isbn", "subjects", "url"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {path}")


# ---------------------------------------------------------------- facets

def show_facets(library, media):
    data = thunder_page(library, 1, 1, media=media, facets=True)
    facets = (data or {}).get("facets") or []
    if not facets:
        print("No facets returned. Fall back to --query with a genre word, "
              "or grab a subject name from a Libby URL.")
        return
    for f in facets:
        name = f.get("name") or f.get("id")
        buckets = f.get("buckets") or f.get("items") or []
        if not buckets:
            continue
        print(f"\n== {name} ==")
        for b in buckets[:40]:
            label = b.get("name") or b.get("label") or b.get("id")
            print(f"  {b.get('count', ''):>7}  {label}")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--library", default="kcls", help="OverDrive slug (default kcls)")
    p.add_argument("--subject", help='genre, e.g. "Science Fiction"')
    p.add_argument("--query", help="free-text search instead of/alongside subject")
    p.add_argument("--media", choices=["ebook", "audiobook", "any"],
                   default="ebook")
    p.add_argument("--pages", type=int, default=8)
    p.add_argument("--per-page", type=int, default=100)
    p.add_argument("--ratings", choices=list(PROVIDERS), default="googlebooks")
    p.add_argument("--min-ratings", type=int, default=25,
                   help="drop titles with fewer than this many ratings")
    p.add_argument("--prior", type=float, default=250.0,
                   help="Bayesian prior weight m; raise it to punish small-N harder")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--csv")
    p.add_argument("--facets", action="store_true",
                   help="list the genre facets this library exposes, then exit")
    a = p.parse_args()

    if a.facets:
        show_facets(a.library, a.media)
        return

    if not (a.subject or a.query):
        p.error("give me a --subject or a --query (or --facets to browse)")

    print(f"harvesting {a.library} · {a.media} · "
          f"{a.subject or a.query} · up to {a.pages * a.per_page} titles")
    recs = harvest(a.library, a.pages, a.per_page, query=a.query,
                   subject=a.subject, media=a.media)
    if not recs:
        print("Nothing available. If this looks wrong, run with --facets to "
              "confirm the subject name, or try --media any.")
        return

    print(f"looking up ratings via {a.ratings} (cached at {CACHE_PATH})")
    con = cache_open()
    fn = PROVIDERS[a.ratings]
    for i, r in enumerate(recs, 1):
        try:
            res = fn(r, con)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            res = {"rating": None, "n": 0}
        r["rating"], r["n_ratings"] = res["rating"], res["n"]
        if i % 25 == 0:
            sys.stderr.write(f"\r  {i}/{len(recs)}")
            sys.stderr.flush()
    sys.stderr.write("\n")
    con.close()

    keep = [r for r in recs
            if r.get("rating") and (r.get("n_ratings") or 0) >= a.min_ratings]
    keep = bayesian(keep, a.prior)
    keep.sort(key=lambda r: r.get("score") or 0, reverse=True)

    show_table(keep, a.top)
    if a.csv:
        write_csv(keep, a.csv)


if __name__ == "__main__":
    main()
