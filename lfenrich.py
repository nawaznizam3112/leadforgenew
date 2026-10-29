"""Email + phone enrichment from PUBLIC sources, with honest quality labels.

Email status ladder (best -> worst):
  verified_public   address is literally published on the company's site (matches the person, or was provided and found)
  verified_manual   a human confirmed/edited it in the Review Queue
  verified_provider a paid provider (Hunter) confirmed the mailbox as deliverable
  inferred_high     company's email format proven by several published addresses + domain has MX (a strong guess)
  accept_all_risky  provider says the server accepts any address, so it can't be confirmed
  inferred_medium   format seen once, domain has MX
  unverified        provided but not confirmed anywhere
  guess             first.last default, only if you opt in; NEVER sold as verified
  none / invalid

We never probe mailboxes over SMTP (cloud hosts block it, it burns IP reputation, catch-all servers lie).
Phones: only valid numbers published on the company's own pages. Direct vs main line is labelled.
"""
import re
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib import robotparser
from urllib.parse import unquote, urlparse

import requests

import lf_store as db
import lf_verify as verify

try:
    import phonenumbers
    from phonenumbers import PhoneNumberFormat, PhoneNumberMatcher, PhoneNumberType
    from phonenumbers import carrier as _carrier
except ImportError:  # phones then report "unavailable"
    phonenumbers = None

UA = {"User-Agent": "Mozilla/5.0 (compatible; LeadForgeBot/1.0; +public business contact verification)"}
PATHS = ("", "/contact", "/contact-us", "/about", "/team", "/leadership")

VERIFIED = {"verified_public", "verified_manual", "verified_provider"}
LIKELY = {"inferred_high", "accept_all_risky"}

# ---------------------------------------------------------------- patterns
PATTERNS = {  # same {placeholder} syntax Hunter uses for domain patterns
    "first.last": "{first}.{last}", "firstlast": "{first}{last}", "flast": "{f}{last}",
    "f.last": "{f}.{last}", "first": "{first}", "last": "{last}", "first_last": "{first}_{last}",
    "first-last": "{first}-{last}", "firstl": "{first}{l}", "lastf": "{last}{f}", "last.first": "{last}.{first}",
}
ROLE_WORDS = {"info", "sales", "contact", "hello", "support", "team", "admin", "office", "hr", "jobs", "careers",
              "press", "media", "marketing", "billing", "help", "service", "legal", "finance", "accounting",
              "privacy", "security", "webmaster", "noreply", "no-reply", "enquiries", "inquiries", "customer",
              "orders", "booking", "reception", "general", "mail", "email", "partners", "feedback"}
SHAPES = [(re.compile(r"^[a-z]{2,}\.[a-z]{2,}$"), "first.last"), (re.compile(r"^[a-z]\.[a-z]{2,}$"), "f.last"),
          (re.compile(r"^[a-z]{2,}_[a-z]{2,}$"), "first_last"), (re.compile(r"^[a-z]{2,}-[a-z]{2,}$"), "first-last")]
TITLES = {"dr", "mr", "mrs", "ms", "prof", "sir", "mba", "phd", "jr", "sr", "ii", "iii", "md", "cpa", "eng"}
BAD_TLD = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "woff", "woff2"}
FIND_EMAILS = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")


def _ascii(s):
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def parse_name(full):
    parts = [p for p in re.split(r"[\s,]+", (full or "").strip()) if p and p.lower().strip(".") not in TITLES]
    if len(parts) < 2:
        return None
    f, l = _ascii(parts[0]), _ascii(parts[-1])
    return (f, l) if f and l else None


def render(template, f, l):
    return template.replace("{first}", f).replace("{last}", l).replace("{f}", f[:1]).replace("{l}", l[:1])


def match_pattern(local, f, l):
    for key, tpl in PATTERNS.items():
        if render(tpl, f, l) == local:
            return key
    return None


def infer_pattern(shape_locals, known_pairs):
    """Vote for the company's email format. known_pairs: [(full_name, email)] proven pairs (weight 2).
    shape_locals: personal-looking local parts published on the site (weight 1). Returns (key, weight, evidence)."""
    votes, evidence = Counter(), {}
    for name, email in known_pairs:
        p = parse_name(name)
        if p and "@" in email:
            key = match_pattern(email.split("@")[0].lower(), *p)
            if key:
                votes[key] += 2
                evidence.setdefault(key, []).append(email)
    for local in shape_locals:
        if any(part in ROLE_WORDS for part in re.split(r"[._\-]", local)):
            continue
        for rx, key in SHAPES:
            if rx.match(local):
                votes[key] += 1
                evidence.setdefault(key, []).append(local)
                break
    if not votes:
        return None, 0, []
    key, weight = votes.most_common(1)[0]
    return key, weight, evidence[key]


# ---------------------------------------------------------------- crawling (polite, robots.txt aware)
_robots = {}


def allowed(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(base)
    if rp is None:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=UA, timeout=6)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return rp.can_fetch(UA["User-Agent"], url)


def fetch_page(url):
    try:
        if not allowed(url):
            return None
        r = requests.get(url, headers=UA, timeout=10)
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
            return None
        return r.text[:1_500_000]
    except Exception:
        return None


def html_to_text(html):
    h = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    h = re.sub(r"(?is)<a[^>]*href=[\"']tel:([^\"']+)[\"'][^>]*>", lambda m: " " + unquote(m.group(1)) + " ", h)
    h = re.sub(r"(?is)<a[^>]*href=[\"']mailto:([^\"'?]+)[^\"']*[\"'][^>]*>",
               lambda m: " " + unquote(m.group(1)) + " ", h)
    h = unescape(re.sub(r"(?s)<[^>]+>", " ", h))
    h = re.sub(r"\s*[\[\(]\s*at\s*[\]\)]\s*", "@", h, flags=re.I)
    h = re.sub(r"\s*[\[\(]\s*dot\s*[\]\)]\s*", ".", h, flags=re.I)
    return re.sub(r"\s+", " ", h).strip()


def harvest_emails(pages, domain):
    out = {}
    for pg in pages:
        for e in FIND_EMAILS.findall(pg["text"].lower()):
            e = e.rstrip(".")
            d = e.split("@", 1)[1]
            if d.rsplit(".", 1)[-1] in BAD_TLD:
                continue
            if d == domain or d.endswith("." + domain):
                out.setdefault(e, pg["url"])
    return out


def crawl_domain(domain, fetch_html=None, extra_urls=(), paths=PATHS, max_pages=4, pause=0.4):
    """Fetch a handful of public pages from one domain. Returns {domain, pages, emails}."""
    fetch_html = fetch_html or fetch_page
    pages, base = [], None
    for path in paths:
        if len(pages) >= max_pages:
            break
        for b in ([base] if base else [f"https://{domain}", f"https://www.{domain}"]):
            html = fetch_html(b + path)
            if pause:
                time.sleep(pause)
            if html:
                base = b
                pages.append({"url": b + path, "text": html_to_text(html)})
                break
        if path == "" and base is None:
            break  # root unreachable: don't hammer a dead site
    for u in extra_urls:
        if u and u not in {p["url"] for p in pages}:
            html = fetch_html(u)
            if html:
                pages.append({"url": u, "text": html_to_text(html)})
    return {"domain": domain, "pages": pages, "emails": harvest_emails(pages, domain)}


# ---------------------------------------------------------------- Hunter (optional, paid; 1 credit per call)
class Hunter:
    base = "https://api.hunter.io/v2"

    def __init__(self, api_key, budget=50):
        self.key, self.budget, self.calls, self.last_error = api_key, budget, 0, ""

    def _get(self, path, **params):
        if self.calls >= self.budget:
            self.last_error = "Hunter call budget reached"
            return None
        self.calls += 1
        try:
            r = requests.get(f"{self.base}/{path}", params={**params, "api_key": self.key}, timeout=20)
            if r.status_code != 200:
                self.last_error = f"Hunter HTTP {r.status_code} (check key / credits)"
                return None
            return r.json().get("data")
        except Exception as e:
            self.last_error = str(e)
            return None

    def verify(self, email):
        d = self._get("email-verifier", email=email)
        return (d or {}).get("status")  # valid / invalid / accept_all / webmail / disposable / unknown

    def find(self, domain, first, last):
        d = self._get("email-finder", domain=domain, first_name=first, last_name=last)
        if not d or not d.get("email"):
            return None
        status = (d.get("verification") or {}).get("status")
        if status is None:
            status = self.verify(d["email"])
        return {"email": d["email"].lower(), "score": d.get("score"), "status": status}


# ---------------------------------------------------------------- email enrichment
def enrich_email(lead, crawl, known=(), hunter=None, allow_guess=False, mx=None, suppressed=()):
    mx = mx or verify.has_mx
    res = {"email": "", "email_status": "none", "email_source": "", "notes": []}
    domain = lead.get("domain") or verify.domain_of(lead.get("website") or "")
    existing = (lead.get("email") or "").strip().lower()
    name = parse_name(lead.get("contact_name"))
    pub = (crawl or {}).get("emails", {})

    def done(email, status, source):
        res.update(email=email, email_status=status, email_source=source)
        return res

    # 1. an email we already have
    if existing and verify.valid_syntax(existing):
        if existing in pub:
            return done(existing, "verified_public", "public_page: " + pub[existing])
        if hunter:
            s = hunter.verify(existing)
            if s == "valid":
                return done(existing, "verified_provider", "hunter")
            if s == "accept_all":
                return done(existing, "accept_all_risky", "hunter")
            if s == "invalid":
                res["notes"].append(f"{existing} rejected by Hunter")
                existing = ""
        if existing:
            return done(existing, "unverified" if mx(verify.domain_of(existing)) else "invalid", "provided")

    if not name or not domain:
        return res
    f, l = name

    # 2. the person's own address published on the site
    for e, src in pub.items():
        if e in suppressed:
            continue
        local = e.split("@")[0]
        if match_pattern(local, f, l) or (len(l) >= 4 and l in local and (f in local or local[:1] == f[:1])):
            return done(e, "verified_public", "public_page: " + src)

    # 3. infer the company format from published addresses
    pairs = list(known)
    key, weight, ev = infer_pattern([e.split("@")[0] for e in pub], pairs)
    if key and mx(domain):
        cand = render(PATTERNS[key], f, l) + "@" + domain
        if cand not in suppressed:
            status = "inferred_high" if weight >= 3 else "inferred_medium"
            source = f"pattern {key} from {len(ev)} example(s)"
            if hunter:
                s = hunter.verify(cand)
                if s == "valid":
                    return done(cand, "verified_provider", "hunter (pattern " + key + ")")
                if s == "accept_all":
                    return done(cand, "accept_all_risky", "hunter (pattern " + key + ")")
                if s == "invalid":
                    res["notes"].append(f"inferred {cand} rejected by Hunter")
                    cand = ""
            if cand:
                return done(cand, status, source)
    elif key:
        res["notes"].append("domain has no MX record; skipped inference")

    # 4. paid finder
    if hunter:
        h = hunter.find(domain, f, l)
        if h and h["email"] not in suppressed:
            st = {"valid": "verified_provider", "accept_all": "accept_all_risky"}.get(h["status"], "inferred_medium")
            if h["status"] != "invalid":
                return done(h["email"], st, f"hunter finder (score {h['score']})")

    # 5. opt-in low-confidence guess
    if allow_guess and mx(domain):
        return done(render(PATTERNS["first.last"], f, l) + "@" + domain, "guess", "default first.last guess")
    return res


# ---------------------------------------------------------------- phone enrichment
COUNTRY_ISO = {"united states": "US", "usa": "US", "us": "US", "united kingdom": "GB", "uk": "GB", "india": "IN",
               "germany": "DE", "france": "FR", "netherlands": "NL", "spain": "ES", "italy": "IT", "canada": "CA",
               "australia": "AU", "singapore": "SG", "uae": "AE", "united arab emirates": "AE", "brazil": "BR",
               "mexico": "MX", "south africa": "ZA", "ireland": "IE", "sweden": "SE", "norway": "NO",
               "denmark": "DK", "finland": "FI", "belgium": "BE", "switzerland": "CH", "austria": "AT",
               "poland": "PL", "portugal": "PT", "japan": "JP", "new zealand": "NZ", "malaysia": "MY",
               "indonesia": "ID", "philippines": "PH", "thailand": "TH", "nigeria": "NG", "kenya": "KE",
               "turkey": "TR", "greece": "GR", "israel": "IL", "saudi arabia": "SA", "egypt": "EG"}
TLD_ISO = {"uk": "GB", "de": "DE", "fr": "FR", "in": "IN", "nl": "NL", "es": "ES", "it": "IT", "ca": "CA",
           "au": "AU", "sg": "SG", "ae": "AE", "br": "BR", "mx": "MX", "za": "ZA", "ie": "IE", "se": "SE",
           "no": "NO", "dk": "DK", "fi": "FI", "be": "BE", "ch": "CH", "at": "AT", "pl": "PL", "pt": "PT",
           "jp": "JP", "nz": "NZ"}


def region_for(lead):
    c = (lead.get("country") or "").strip()
    if len(c) == 2 and c.isalpha():
        return c.upper()
    if c.lower() in COUNTRY_ISO:
        return COUNTRY_ISO[c.lower()]
    return TLD_ISO.get((lead.get("domain") or "").rsplit(".", 1)[-1])


def _type_name(t):
    names = {PhoneNumberType.MOBILE: "mobile", PhoneNumberType.FIXED_LINE: "landline",
             PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline_or_mobile", PhoneNumberType.TOLL_FREE: "toll_free",
             PhoneNumberType.VOIP: "voip"}
    return names.get(t, "other")


def find_numbers(text, region):
    """All VALID phone numbers in text (needs the `phonenumbers` package)."""
    if phonenumbers is None:
        return []
    out = []
    try:
        for m in PhoneNumberMatcher(text, region):
            n = m.number
            if phonenumbers.is_valid_number(n):
                out.append({"start": m.start, "end": m.end, "e164": phonenumbers.format_number(n, PhoneNumberFormat.E164),
                            "region": phonenumbers.region_code_for_number(n),
                            "line": _type_name(phonenumbers.number_type(n)),
                            "carrier": _carrier.name_for_number(n, "en") or ""})
    except Exception:
        return []
    return out


def enrich_phone(lead, crawl, finder=None):
    """Pick the best PUBLIC business number: one printed next to the person's name beats a main line."""
    finder = finder or find_numbers
    res = {"phone": "", "phone_kind": "", "phone_status": "none", "notes": []}
    if finder is find_numbers and phonenumbers is None:
        res["phone_status"] = "unavailable"
        res["notes"].append("install `phonenumbers` to enable phone extraction")
        return res
    region, name = region_for(lead), parse_name(lead.get("contact_name"))
    best = None
    for pg in (crawl or {}).get("pages", []):
        low = pg["text"].lower()
        for n in finder(pg["text"], region):
            near = False
            if name:
                win = low[max(0, n["start"] - 200): n["end"] + 200]
                near = name[1] in win and name[0] in win
            score = 2 if near else 1
            if region and n.get("region") and n["region"] != region:
                score -= 0.5  # foreign number on a local company page: probably HQ elsewhere
            if best is None or score > best[0]:
                best = (score, n, pg["url"], near)
    if best:
        _, n, url, near = best
        res.update(phone=n["e164"], phone_kind=("direct_listed" if near else "company_main") + "/" + n["line"],
                   phone_status="verified_public")
        res["notes"].append("phone from " + url)
    return res


# ---------------------------------------------------------------- orchestration
def effective_email_status(lead):
    email = (lead.get("email") or "").strip()
    if not email:
        return "none"
    if lead.get("email_status"):
        return lead["email_status"]
    flags = lead.get("flags") or ""  # legacy rows from before enrichment existed
    return "unverified" if ("email_not_on_source" in flags or "no_mx_record" in flags) else "verified_public"


def filter_quality(leads, quality):
    if quality == "verified":
        return [l for l in leads if effective_email_status(l) in VERIFIED]
    if quality == "likely":
        return [l for l in leads if effective_email_status(l) in VERIFIED | LIKELY]
    return list(leads)


def enrich_lead(lead, crawl, known=(), hunter=None, allow_guess=False, find_phone=True, mx=None,
                suppressed=(), phone_finder=None):
    e = enrich_email(lead, crawl, known, hunter, allow_guess, mx, suppressed)
    p = enrich_phone(lead, crawl, phone_finder) if find_phone else \
        {"phone": "", "phone_kind": "", "phone_status": "none", "notes": []}
    return {**e, **{k: p[k] for k in ("phone", "phone_kind", "phone_status")}, "notes": e["notes"] + p["notes"]}


def enrich_order(order_id, hunter=None, allow_guess=False, find_phone=True, redo=False, suppressed=(),
                 progress=None, crawl_fn=None, mx=None, phone_finder=None, workers=6):
    sql = ("SELECT * FROM leads WHERE order_id=? AND COALESCE(stale,0)=0 "
           "AND status IN ('verified','auto_verified','needs_review')")
    leads = db.q(sql + ("" if redo else " AND enriched_at IS NULL"), (order_id,))
    crawl_fn = crawl_fn or (lambda d, extra: crawl_domain(d, extra_urls=extra))
    by_domain = {}
    for l in leads:
        by_domain.setdefault(l["domain"], set()).add(l.get("source_url") or "")

    crawls, done = {}, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(crawl_fn, d, sorted(u for u in urls if u)): d for d, urls in by_domain.items()}
        for fut, d in futs.items():
            try:
                crawls[d] = fut.result()
            except Exception:
                crawls[d] = {"domain": d, "pages": [], "emails": {}}
            done += 1
            if progress:
                progress(0.7 * done / max(len(futs), 1))

    summary = Counter()
    for i, l in enumerate(leads):
        known = [(r["contact_name"], r["email"]) for r in db.q(
            "SELECT contact_name, email FROM leads WHERE domain=? AND email!='' AND id!=?", (l["domain"], l["id"]))]
        r = enrich_lead(l, crawls.get(l["domain"]), known, hunter, allow_guess, find_phone, mx,
                        suppressed, phone_finder)
        flags = ",".join(f for f in (l.get("flags") or "").split(",") if f and not (f == "no_email" and r["email"]))
        status = l["status"]
        if (status == "needs_review" and r["email_status"] in {"verified_public", "verified_provider", "inferred_high"}
                and "name_not_on_source" not in flags and "source_unreachable" not in flags):
            status = "auto_verified"
        notes = " | ".join(x for x in [l.get("notes") or "", *r["notes"]] if x)[:500]
        db.x("UPDATE leads SET email=?, email_status=?, email_source=?, phone=?, phone_kind=?, phone_status=?, "
             "enriched_at=datetime('now'), notes=?, flags=?, status=? WHERE id=?",
             (r["email"], r["email_status"], r["email_source"], r["phone"], r["phone_kind"], r["phone_status"],
              notes, flags, status, l["id"]))
        summary[r["email_status"]] += 1
        summary["phone_found"] += 1 if r["phone"] else 0
        if progress:
            progress(0.7 + 0.3 * (i + 1) / max(len(leads), 1))
    summary["processed"] = len(leads)
    summary["hunter_calls"] = hunter.calls if hunter else 0
    return dict(summary)
