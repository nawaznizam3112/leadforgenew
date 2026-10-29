"""LeadForge — single-file build. Upload only this file + requirements.txt."""
import sys as _sys, types as _types

def _register(name, src):
    m = _types.ModuleType(name)
    m.__file__ = name + '.py'
    _sys.modules[name] = m
    exec(compile(src, name + '.py', 'exec'), m.__dict__)

_register('lf_verify', '"""Lead verification: proves each lead against its public source page.\n\nChecks (no SMTP probing, no private data): source page reachable, person\'s name\nand title appear on it, email syntax + MX record, email literally on the page,\ndisposable/role-address flags. Produces a 0-100 confidence score.\n"""\nimport re\nfrom urllib.parse import urlparse\n\nimport requests\n\ntry:\n    import dns.resolver\nexcept ImportError:  # pragma: no cover\n    dns = None\n\nEMAIL_RE = re.compile(r"^[a-z0-9._%+\\-]+@[a-z0-9.\\-]+\\.[a-z]{2,}$")\nFIND_EMAILS = re.compile(r"[a-z0-9._%+\\-]+@[a-z0-9.\\-]+\\.[a-z]{2,}")\nDISPOSABLE = {"mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com",\n              "yopmail.com", "trashmail.com", "sharklasers.com"}\nROLE_PREFIXES = {"info", "sales", "contact", "admin", "support", "hello", "office",\n                 "team", "help", "noreply", "no-reply"}\nUA = {"User-Agent": "Mozilla/5.0 (compatible; LeadForgeVerifier/1.0)"}\n\n\ndef valid_syntax(email):\n    return bool(EMAIL_RE.match((email or "").strip().lower()))\n\n\ndef domain_of(value):\n    value = (value or "").strip().lower()\n    if "@" in value:\n        return value.split("@", 1)[1]\n    if "://" not in value:\n        value = "http://" + value\n    host = urlparse(value).netloc\n    return host[4:] if host.startswith("www.") else host\n\n\ndef has_mx(domain):\n    if not domain or dns is None:\n        return False\n    try:\n        return len(dns.resolver.resolve(domain, "MX", lifetime=5)) > 0\n    except Exception:\n        return False\n\n\ndef fetch_text(url):\n    """Return lowercased visible-ish text of a page, or None if unreachable."""\n    if not url:\n        return None\n    try:\n        r = requests.get(url, headers=UA, timeout=10)\n        if r.status_code >= 400:\n            return None\n        html = re.sub(r"(?is)<(script|style).*?</\\1>", " ", r.text)\n        text = re.sub(r"(?s)<[^>]+>", " ", html)\n        return re.sub(r"\\s+", " ", text).lower()\n    except Exception:\n        return None\n\n\ndef check_lead(lead, fetch=None, mx=None):\n    """Return dict(confidence, status, flags, notes, email)."""\n    fetch = fetch or fetch_text\n    mx = mx or has_mx\n    score, flags, notes = 0, [], ""\n    email = (lead.get("email") or "").strip().lower()\n    page = fetch(lead.get("source_url") or "")\n\n    if page:\n        score += 30\n    else:\n        flags.append("source_unreachable")\n\n    tokens = [t for t in re.findall(r"[^\\W\\d_]+", (lead.get("contact_name") or "").lower()) if len(t) > 1]\n    if page and tokens and tokens[0] in page and tokens[-1] in page:\n        score += 30\n    else:\n        flags.append("name_not_on_source")\n\n    title = (lead.get("title") or "").strip().lower()\n    if page and title and title in page:\n        score += 10\n    else:\n        flags.append("title_not_on_source")\n\n    if email:\n        if not valid_syntax(email):\n            flags.append("bad_email_syntax")\n            email = ""\n        else:\n            edomain = domain_of(email)\n            if page and email in page:\n                score += 20\n            else:\n                flags.append("email_not_on_source")\n            if mx(edomain):\n                score += 10\n            else:\n                flags.append("no_mx_record")\n            if edomain in DISPOSABLE:\n                flags.append("disposable_domain")\n                score -= 30\n            if email.split("@")[0] in ROLE_PREFIXES:\n                flags.append("role_email")\n    else:\n        flags.append("no_email")\n        if page:\n            seen = sorted(set(FIND_EMAILS.findall(page)))[:3]\n            if seen:\n                notes = "Emails seen on source page (unassigned): " + ", ".join(seen)\n\n    score = max(0, min(100, score))\n    # A person\'s name must appear on the source page, otherwise a human must check.\n    ok = score >= 70 and email and "name_not_on_source" not in flags and "source_unreachable" not in flags\n    status = "auto_verified" if ok else "needs_review"\n    return {"confidence": score, "status": status, "flags": ",".join(flags),\n            "notes": notes, "email": email}\n')

_register('lf_store', '"""SQLite storage. Set DB_PATH to a persistent volume in production."""\nimport os\nimport sqlite3\n\nDB_PATH = os.getenv("DB_PATH", "leadforge.db")\n\nSCHEMA = """\nCREATE TABLE IF NOT EXISTS clients(\n  id INTEGER PRIMARY KEY, name TEXT NOT NULL, company TEXT, email TEXT,\n  created TEXT DEFAULT (datetime(\'now\')));\nCREATE TABLE IF NOT EXISTS orders(\n  id INTEGER PRIMARY KEY, client_id INTEGER, title TEXT, icp_json TEXT,\n  target_count INTEGER, price_usd REAL DEFAULT 0, plan TEXT DEFAULT \'one_time\',\n  status TEXT DEFAULT \'draft\', paid INTEGER DEFAULT 0, active INTEGER DEFAULT 1,\n  cost_usd REAL DEFAULT 0, review_minutes REAL DEFAULT 0,\n  created TEXT DEFAULT (datetime(\'now\')), delivered_at TEXT);\nCREATE TABLE IF NOT EXISTS leads(\n  id INTEGER PRIMARY KEY, order_id INTEGER, company TEXT, website TEXT, domain TEXT,\n  contact_name TEXT, title TEXT, email TEXT, country TEXT, source_url TEXT,\n  confidence INTEGER DEFAULT 0, status TEXT DEFAULT \'new\', flags TEXT, notes TEXT,\n  created TEXT DEFAULT (datetime(\'now\')));\nCREATE TABLE IF NOT EXISTS refreshes(\n  id INTEGER PRIMARY KEY, order_id INTEGER, run_at TEXT DEFAULT (datetime(\'now\')),\n  checked INTEGER DEFAULT 0, ok INTEGER DEFAULT 0, stale INTEGER DEFAULT 0,\n  unconfirmed INTEGER DEFAULT 0, manual INTEGER DEFAULT 0, replaced INTEGER DEFAULT 0);\nCREATE TABLE IF NOT EXISTS suppression(\n  id INTEGER PRIMARY KEY, value TEXT UNIQUE, reason TEXT,\n  created TEXT DEFAULT (datetime(\'now\')));\n"""\n\n\ndef conn():\n    c = sqlite3.connect(DB_PATH)\n    c.row_factory = sqlite3.Row\n    return c\n\n\nMIGRATIONS = [  # safe to re-run; adds columns to databases created by older versions\n    "ALTER TABLE leads ADD COLUMN last_checked TEXT",\n    "ALTER TABLE leads ADD COLUMN stale INTEGER DEFAULT 0",\n    "ALTER TABLE leads ADD COLUMN stale_reason TEXT",\n    "ALTER TABLE leads ADD COLUMN replaces_id INTEGER",\n    "ALTER TABLE orders ADD COLUMN last_refresh TEXT",\n    "ALTER TABLE leads ADD COLUMN email_status TEXT",\n    "ALTER TABLE leads ADD COLUMN email_source TEXT",\n    "ALTER TABLE leads ADD COLUMN phone TEXT",\n    "ALTER TABLE leads ADD COLUMN phone_kind TEXT",\n    "ALTER TABLE leads ADD COLUMN phone_status TEXT",\n    "ALTER TABLE leads ADD COLUMN enriched_at TEXT",\n]\n\n\ndef init():\n    with conn() as c:\n        c.executescript(SCHEMA)\n        for m in MIGRATIONS:\n            try:\n                c.execute(m)\n            except sqlite3.OperationalError:\n                pass  # column already exists\n\n\ndef q(sql, args=()):\n    with conn() as c:\n        return [dict(r) for r in c.execute(sql, args).fetchall()]\n\n\ndef x(sql, args=()):\n    with conn() as c:\n        return c.execute(sql, args).lastrowid\n')

_register('lf_refresh', '"""Monthly refresh: re-verify delivered leads, flag stale ones, source replacements, build client report."""\nimport re\nimport time\nfrom io import BytesIO\n\nimport lf_store as db\nimport lf_verify as verify\n\nSTALE_STATES = ("departed", "email_dead", "role_changed")\n\n\ndef _tokens(name):\n    return [t for t in re.findall(r"[^\\W\\d_]+", (name or "").lower()) if len(t) > 1]\n\n\ndef recheck_lead(lead, get_page, mx=None):\n    """Return (state, reason). States: ok, departed, email_dead, role_changed, unconfirmed, manual."""\n    mx = mx or verify.has_mx\n    flags = lead.get("flags") or ""\n    if "name_not_on_source" in flags or "source_unreachable" in flags:\n        return "manual", "originally verified by hand (source not machine-readable); re-check manually"\n    page = get_page(lead.get("source_url") or "")\n    if page is None:\n        return "unconfirmed", "source page unreachable this time"\n    if len(page) < 200:\n        return "unconfirmed", "source page returned too little text (blocked or JS-rendered)"\n    tokens = _tokens(lead.get("contact_name"))\n    if not tokens or tokens[0] not in page or tokens[-1] not in page:\n        return "departed", "name no longer appears on the source page"\n    email = (lead.get("email") or "").lower()\n    if email and not mx(verify.domain_of(email)):\n        return "email_dead", "email domain no longer accepts mail"\n    title = (lead.get("title") or "").strip().lower()\n    if title and "title_not_on_source" not in flags and title not in page:\n        return "role_changed", f"title \'{lead.get(\'title\')}\' no longer appears on the source page"\n    return "ok", ""\n\n\ndef run_refresh(order_id, fetch=None, mx=None, progress=None, pause=0.3):\n    """Re-check every verified, non-stale lead of an order. Updates DB, returns summary dict."""\n    fetch = fetch or verify.fetch_text\n    leads = db.q("SELECT * FROM leads WHERE order_id=? AND status=\'verified\' AND COALESCE(stale,0)=0",\n                 (order_id,))\n    run_id = db.x("INSERT INTO refreshes(order_id) VALUES(?)", (order_id,))\n    cache = {}\n\n    def get_page(url):\n        if url in cache:\n            return cache[url]\n        page = fetch(url) or fetch(url)  # one retry for flaky pages\n        cache[url] = page\n        time.sleep(pause)\n        return page\n\n    counts = {"ok": 0, "stale": 0, "unconfirmed": 0, "manual": 0}\n    for i, lead in enumerate(leads):\n        state, reason = recheck_lead(lead, get_page, mx)\n        if state in STALE_STATES:\n            db.x("UPDATE leads SET stale=1, status=\'stale\', stale_reason=?, last_checked=datetime(\'now\') "\n                 "WHERE id=?", (reason, lead["id"]))\n            counts["stale"] += 1\n        else:\n            db.x("UPDATE leads SET last_checked=datetime(\'now\'), stale_reason=? WHERE id=?",\n                 (reason if state != "ok" else "", lead["id"]))\n            counts[state] += 1\n        if progress:\n            progress((i + 1) / max(len(leads), 1))\n    db.x("UPDATE refreshes SET checked=?, ok=?, stale=?, unconfirmed=?, manual=? WHERE id=?",\n         (len(leads), counts["ok"], counts["stale"], counts["unconfirmed"], counts["manual"], run_id))\n    db.x("UPDATE orders SET last_refresh=datetime(\'now\') WHERE id=?", (order_id,))\n    return {"run_id": run_id, "checked": len(leads), **counts}\n\n\ndef unreplaced_stale(order_id):\n    return db.q("SELECT id FROM leads WHERE order_id=? AND stale=1 AND id NOT IN "\n                "(SELECT replaces_id FROM leads WHERE replaces_id IS NOT NULL) ORDER BY id", (order_id,))\n\n\ndef replace_stale(order_id, icp, discover, suppressed=(), max_batches=8, pause=2, progress=None):\n    """Source + auto-check new leads for each stale one. `discover(icp, known_domains, k)` -> list of dicts.\n    Replacements land in the review queue (auto_verified / needs_review); a human still approves them."""\n    todo = unreplaced_stale(order_id)\n    known = {r["domain"] for r in db.q("SELECT domain FROM leads WHERE order_id=?", (order_id,))}\n    added = 0\n    for batch_no in range(max_batches):\n        if not todo:\n            break\n        try:\n            batch = discover(icp, known, 8)\n        except Exception:\n            time.sleep(5)\n            continue\n        for b in batch:\n            if not todo:\n                break\n            dom = verify.domain_of(b.get("website", ""))\n            email = (b.get("email") or "").lower()\n            if not dom or dom in known or dom in suppressed or email in suppressed or not b.get("contact_name"):\n                continue\n            known.add(dom)\n            res = verify.check_lead(b)\n            old = todo.pop(0)\n            db.x("INSERT INTO leads(order_id,company,website,domain,contact_name,title,email,country,"\n                 "source_url,confidence,status,flags,notes,replaces_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",\n                 (order_id, b.get("company"), b.get("website"), dom, b.get("contact_name"), b.get("title"),\n                  res["email"], b.get("country"), b.get("source_url"), res["confidence"], res["status"],\n                  res["flags"], res["notes"] or b.get("why_fit", ""), old["id"]))\n            added += 1\n        if progress:\n            progress(min(1.0, (batch_no + 1) / max_batches))\n        time.sleep(pause)\n    if added:\n        db.x("UPDATE refreshes SET replaced=replaced+? WHERE id=(SELECT MAX(id) FROM refreshes WHERE order_id=?)",\n             (added, order_id))\n    return added\n\n\ndef decay_stats():\n    r = db.q("SELECT COALESCE(SUM(checked),0) c, COALESCE(SUM(stale),0) s, COUNT(*) n FROM refreshes")[0]\n    return {"checked": r["c"], "stale": r["s"], "runs": r["n"],\n            "decay_pct": round(r["s"] / r["c"] * 100, 1) if r["c"] else 0.0}\n\n\ndef build_report(order_id):\n    """Return (client_message_text, xlsx_bytes) for the latest refresh of an order."""\n    import pandas as pd\n    order = db.q("SELECT o.*, c.name client FROM orders o LEFT JOIN clients c ON c.id=o.client_id "\n                 "WHERE o.id=?", (order_id,))[0]\n    run = db.q("SELECT * FROM refreshes WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,))\n    if not run:\n        raise ValueError("Run a refresh first")\n    run = run[0]\n    cols = "company, contact_name, title, email, country, source_url"\n    stale = db.q(f"SELECT id, {cols}, stale_reason FROM leads WHERE order_id=? AND stale=1 "\n                 "AND last_checked >= ?", (order_id, run["run_at"]))\n    ids = [s["id"] for s in stale]\n    repl = db.q(f"SELECT {cols}, confidence, replaces_id FROM leads WHERE order_id=? AND status=\'verified\' "\n                "AND replaces_id IS NOT NULL", (order_id,))\n    repl = [r for r in repl if r["replaces_id"] in ids]\n    pending = db.q("SELECT COUNT(*) n FROM leads WHERE order_id=? AND replaces_id IS NOT NULL "\n                   "AND status IN (\'new\',\'needs_review\',\'auto_verified\')", (order_id,))[0]["n"]\n    valid = db.q(f"SELECT {cols}, last_checked FROM leads WHERE order_id=? AND status=\'verified\' "\n                 "AND COALESCE(stale,0)=0 AND replaces_id IS NULL", (order_id,))\n\n    pct = round(run["stale"] / run["checked"] * 100) if run["checked"] else 0\n    lines = [f"Hi {order[\'client\'] or \'there\'},", "",\n             f"Your monthly refresh for \\"{order[\'title\']}\\" is ready. We re-checked {run[\'checked\']} contacts "\n             f"against their original public sources.", "",\n             f"• Still valid: {run[\'ok\']}",\n             f"• Went stale: {run[\'stale\']} ({pct}%) — the person or title no longer matches the source page, "\n             f"or the email domain stopped accepting mail",\n             f"• Verified replacements attached: {len(repl)}"]\n    if pending:\n        lines.append(f"• Replacements still being verified by our team: {pending} (we\'ll send them shortly)")\n    if run["unconfirmed"] + run["manual"]:\n        lines.append(f"• Couldn\'t be re-checked automatically (source unreachable or manually verified): "\n                     f"{run[\'unconfirmed\'] + run[\'manual\']} — kept in your list, flagged for a manual check")\n    lines += ["", "The attached workbook lists every stale contact with the reason and its replacement.",\n              "Reply to this email if you\'d like any replaced contact reviewed again.", "", "— LeadForge"]\n    text = "\\n".join(lines)\n\n    summary = pd.DataFrame({"Metric": ["Contacts re-checked", "Still valid", "Went stale", "Stale %",\n                                       "Verified replacements", "Replacements pending", "Not auto-checkable"],\n                            "Value": [run["checked"], run["ok"], run["stale"], f"{pct}%", len(repl),\n                                      pending, run["unconfirmed"] + run["manual"]]})\n    stale_df = pd.DataFrame(stale).drop(columns=["id"], errors="ignore")\n    repl_df = pd.DataFrame(repl).drop(columns=["replaces_id"], errors="ignore")\n    buf = BytesIO()\n    with pd.ExcelWriter(buf, engine="openpyxl") as w:\n        summary.to_excel(w, sheet_name="Summary", index=False)\n        stale_df.to_excel(w, sheet_name="Stale contacts", index=False)\n        repl_df.to_excel(w, sheet_name="Replacements", index=False)\n        pd.DataFrame(valid).to_excel(w, sheet_name="Still valid", index=False)\n    return text, buf.getvalue()\n')

_register('lf_enrich', '"""Email + phone enrichment from PUBLIC sources, with honest quality labels.\n\nEmail status ladder (best -> worst):\n  verified_public   address is literally published on the company\'s site (matches the person, or was provided and found)\n  verified_manual   a human confirmed/edited it in the Review Queue\n  verified_provider a paid provider (Hunter) confirmed the mailbox as deliverable\n  inferred_high     company\'s email format proven by several published addresses + domain has MX (a strong guess)\n  accept_all_risky  provider says the server accepts any address, so it can\'t be confirmed\n  inferred_medium   format seen once, domain has MX\n  unverified        provided but not confirmed anywhere\n  guess             first.last default, only if you opt in; NEVER sold as verified\n  none / invalid\n\nWe never probe mailboxes over SMTP (cloud hosts block it, it burns IP reputation, catch-all servers lie).\nPhones: only valid numbers published on the company\'s own pages. Direct vs main line is labelled.\n"""\nimport re\nimport time\nimport unicodedata\nfrom collections import Counter\nfrom concurrent.futures import ThreadPoolExecutor\nfrom html import unescape\nfrom urllib import robotparser\nfrom urllib.parse import unquote, urlparse\n\nimport requests\n\nimport lf_store as db\nimport lf_verify as verify\n\ntry:\n    import phonenumbers\n    from phonenumbers import PhoneNumberFormat, PhoneNumberMatcher, PhoneNumberType\n    from phonenumbers import carrier as _carrier\nexcept ImportError:  # phones then report "unavailable"\n    phonenumbers = None\n\nUA = {"User-Agent": "Mozilla/5.0 (compatible; LeadForgeBot/1.0; +public business contact verification)"}\nPATHS = ("", "/contact", "/contact-us", "/about", "/team", "/leadership")\n\nVERIFIED = {"verified_public", "verified_manual", "verified_provider"}\nLIKELY = {"inferred_high", "accept_all_risky"}\n\n# ---------------------------------------------------------------- patterns\nPATTERNS = {  # same {placeholder} syntax Hunter uses for domain patterns\n    "first.last": "{first}.{last}", "firstlast": "{first}{last}", "flast": "{f}{last}",\n    "f.last": "{f}.{last}", "first": "{first}", "last": "{last}", "first_last": "{first}_{last}",\n    "first-last": "{first}-{last}", "firstl": "{first}{l}", "lastf": "{last}{f}", "last.first": "{last}.{first}",\n}\nROLE_WORDS = {"info", "sales", "contact", "hello", "support", "team", "admin", "office", "hr", "jobs", "careers",\n              "press", "media", "marketing", "billing", "help", "service", "legal", "finance", "accounting",\n              "privacy", "security", "webmaster", "noreply", "no-reply", "enquiries", "inquiries", "customer",\n              "orders", "booking", "reception", "general", "mail", "email", "partners", "feedback"}\nSHAPES = [(re.compile(r"^[a-z]{2,}\\.[a-z]{2,}$"), "first.last"), (re.compile(r"^[a-z]\\.[a-z]{2,}$"), "f.last"),\n          (re.compile(r"^[a-z]{2,}_[a-z]{2,}$"), "first_last"), (re.compile(r"^[a-z]{2,}-[a-z]{2,}$"), "first-last")]\nTITLES = {"dr", "mr", "mrs", "ms", "prof", "sir", "mba", "phd", "jr", "sr", "ii", "iii", "md", "cpa", "eng"}\nBAD_TLD = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "woff", "woff2"}\nFIND_EMAILS = re.compile(r"[a-z0-9._%+\\-]+@[a-z0-9.\\-]+\\.[a-z]{2,}")\n\n\ndef _ascii(s):\n    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())\n\n\ndef parse_name(full):\n    parts = [p for p in re.split(r"[\\s,]+", (full or "").strip()) if p and p.lower().strip(".") not in TITLES]\n    if len(parts) < 2:\n        return None\n    f, l = _ascii(parts[0]), _ascii(parts[-1])\n    return (f, l) if f and l else None\n\n\ndef render(template, f, l):\n    return template.replace("{first}", f).replace("{last}", l).replace("{f}", f[:1]).replace("{l}", l[:1])\n\n\ndef match_pattern(local, f, l):\n    for key, tpl in PATTERNS.items():\n        if render(tpl, f, l) == local:\n            return key\n    return None\n\n\ndef infer_pattern(shape_locals, known_pairs):\n    """Vote for the company\'s email format. known_pairs: [(full_name, email)] proven pairs (weight 2).\n    shape_locals: personal-looking local parts published on the site (weight 1). Returns (key, weight, evidence)."""\n    votes, evidence = Counter(), {}\n    for name, email in known_pairs:\n        p = parse_name(name)\n        if p and "@" in email:\n            key = match_pattern(email.split("@")[0].lower(), *p)\n            if key:\n                votes[key] += 2\n                evidence.setdefault(key, []).append(email)\n    for local in shape_locals:\n        if any(part in ROLE_WORDS for part in re.split(r"[._\\-]", local)):\n            continue\n        for rx, key in SHAPES:\n            if rx.match(local):\n                votes[key] += 1\n                evidence.setdefault(key, []).append(local)\n                break\n    if not votes:\n        return None, 0, []\n    key, weight = votes.most_common(1)[0]\n    return key, weight, evidence[key]\n\n\n# ---------------------------------------------------------------- crawling (polite, robots.txt aware)\n_robots = {}\n\n\ndef allowed(url):\n    p = urlparse(url)\n    base = f"{p.scheme}://{p.netloc}"\n    rp = _robots.get(base)\n    if rp is None:\n        rp = robotparser.RobotFileParser()\n        try:\n            r = requests.get(base + "/robots.txt", headers=UA, timeout=6)\n            rp.parse(r.text.splitlines() if r.status_code == 200 else [])\n        except Exception:\n            rp.parse([])\n        _robots[base] = rp\n    return rp.can_fetch(UA["User-Agent"], url)\n\n\ndef fetch_page(url):\n    try:\n        if not allowed(url):\n            return None\n        r = requests.get(url, headers=UA, timeout=10)\n        if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):\n            return None\n        return r.text[:1_500_000]\n    except Exception:\n        return None\n\n\ndef html_to_text(html):\n    h = re.sub(r"(?is)<(script|style|noscript).*?</\\1>", " ", html)\n    h = re.sub(r"(?is)<a[^>]*href=[\\"\']tel:([^\\"\']+)[\\"\'][^>]*>", lambda m: " " + unquote(m.group(1)) + " ", h)\n    h = re.sub(r"(?is)<a[^>]*href=[\\"\']mailto:([^\\"\'?]+)[^\\"\']*[\\"\'][^>]*>",\n               lambda m: " " + unquote(m.group(1)) + " ", h)\n    h = unescape(re.sub(r"(?s)<[^>]+>", " ", h))\n    h = re.sub(r"\\s*[\\[\\(]\\s*at\\s*[\\]\\)]\\s*", "@", h, flags=re.I)\n    h = re.sub(r"\\s*[\\[\\(]\\s*dot\\s*[\\]\\)]\\s*", ".", h, flags=re.I)\n    return re.sub(r"\\s+", " ", h).strip()\n\n\ndef harvest_emails(pages, domain):\n    out = {}\n    for pg in pages:\n        for e in FIND_EMAILS.findall(pg["text"].lower()):\n            e = e.rstrip(".")\n            d = e.split("@", 1)[1]\n            if d.rsplit(".", 1)[-1] in BAD_TLD:\n                continue\n            if d == domain or d.endswith("." + domain):\n                out.setdefault(e, pg["url"])\n    return out\n\n\ndef crawl_domain(domain, fetch_html=None, extra_urls=(), paths=PATHS, max_pages=4, pause=0.4):\n    """Fetch a handful of public pages from one domain. Returns {domain, pages, emails}."""\n    fetch_html = fetch_html or fetch_page\n    pages, base = [], None\n    for path in paths:\n        if len(pages) >= max_pages:\n            break\n        for b in ([base] if base else [f"https://{domain}", f"https://www.{domain}"]):\n            html = fetch_html(b + path)\n            if pause:\n                time.sleep(pause)\n            if html:\n                base = b\n                pages.append({"url": b + path, "text": html_to_text(html)})\n                break\n        if path == "" and base is None:\n            break  # root unreachable: don\'t hammer a dead site\n    for u in extra_urls:\n        if u and u not in {p["url"] for p in pages}:\n            html = fetch_html(u)\n            if html:\n                pages.append({"url": u, "text": html_to_text(html)})\n    return {"domain": domain, "pages": pages, "emails": harvest_emails(pages, domain)}\n\n\n# ---------------------------------------------------------------- Hunter (optional, paid; 1 credit per call)\nclass Hunter:\n    base = "https://api.hunter.io/v2"\n\n    def __init__(self, api_key, budget=50):\n        self.key, self.budget, self.calls, self.last_error = api_key, budget, 0, ""\n\n    def _get(self, path, **params):\n        if self.calls >= self.budget:\n            self.last_error = "Hunter call budget reached"\n            return None\n        self.calls += 1\n        try:\n            r = requests.get(f"{self.base}/{path}", params={**params, "api_key": self.key}, timeout=20)\n            if r.status_code != 200:\n                self.last_error = f"Hunter HTTP {r.status_code} (check key / credits)"\n                return None\n            return r.json().get("data")\n        except Exception as e:\n            self.last_error = str(e)\n            return None\n\n    def verify(self, email):\n        d = self._get("email-verifier", email=email)\n        return (d or {}).get("status")  # valid / invalid / accept_all / webmail / disposable / unknown\n\n    def find(self, domain, first, last):\n        d = self._get("email-finder", domain=domain, first_name=first, last_name=last)\n        if not d or not d.get("email"):\n            return None\n        status = (d.get("verification") or {}).get("status")\n        if status is None:\n            status = self.verify(d["email"])\n        return {"email": d["email"].lower(), "score": d.get("score"), "status": status}\n\n\n# ---------------------------------------------------------------- email enrichment\ndef enrich_email(lead, crawl, known=(), hunter=None, allow_guess=False, mx=None, suppressed=()):\n    mx = mx or verify.has_mx\n    res = {"email": "", "email_status": "none", "email_source": "", "notes": []}\n    domain = lead.get("domain") or verify.domain_of(lead.get("website") or "")\n    existing = (lead.get("email") or "").strip().lower()\n    name = parse_name(lead.get("contact_name"))\n    pub = (crawl or {}).get("emails", {})\n\n    def done(email, status, source):\n        res.update(email=email, email_status=status, email_source=source)\n        return res\n\n    # 1. an email we already have\n    if existing and verify.valid_syntax(existing):\n        if existing in pub:\n            return done(existing, "verified_public", "public_page: " + pub[existing])\n        if hunter:\n            s = hunter.verify(existing)\n            if s == "valid":\n                return done(existing, "verified_provider", "hunter")\n            if s == "accept_all":\n                return done(existing, "accept_all_risky", "hunter")\n            if s == "invalid":\n                res["notes"].append(f"{existing} rejected by Hunter")\n                existing = ""\n        if existing:\n            return done(existing, "unverified" if mx(verify.domain_of(existing)) else "invalid", "provided")\n\n    if not name or not domain:\n        return res\n    f, l = name\n\n    # 2. the person\'s own address published on the site\n    for e, src in pub.items():\n        if e in suppressed:\n            continue\n        local = e.split("@")[0]\n        if match_pattern(local, f, l) or (len(l) >= 4 and l in local and (f in local or local[:1] == f[:1])):\n            return done(e, "verified_public", "public_page: " + src)\n\n    # 3. infer the company format from published addresses\n    pairs = list(known)\n    key, weight, ev = infer_pattern([e.split("@")[0] for e in pub], pairs)\n    if key and mx(domain):\n        cand = render(PATTERNS[key], f, l) + "@" + domain\n        if cand not in suppressed:\n            status = "inferred_high" if weight >= 3 else "inferred_medium"\n            source = f"pattern {key} from {len(ev)} example(s)"\n            if hunter:\n                s = hunter.verify(cand)\n                if s == "valid":\n                    return done(cand, "verified_provider", "hunter (pattern " + key + ")")\n                if s == "accept_all":\n                    return done(cand, "accept_all_risky", "hunter (pattern " + key + ")")\n                if s == "invalid":\n                    res["notes"].append(f"inferred {cand} rejected by Hunter")\n                    cand = ""\n            if cand:\n                return done(cand, status, source)\n    elif key:\n        res["notes"].append("domain has no MX record; skipped inference")\n\n    # 4. paid finder\n    if hunter:\n        h = hunter.find(domain, f, l)\n        if h and h["email"] not in suppressed:\n            st = {"valid": "verified_provider", "accept_all": "accept_all_risky"}.get(h["status"], "inferred_medium")\n            if h["status"] != "invalid":\n                return done(h["email"], st, f"hunter finder (score {h[\'score\']})")\n\n    # 5. opt-in low-confidence guess\n    if allow_guess and mx(domain):\n        return done(render(PATTERNS["first.last"], f, l) + "@" + domain, "guess", "default first.last guess")\n    return res\n\n\n# ---------------------------------------------------------------- phone enrichment\nCOUNTRY_ISO = {"united states": "US", "usa": "US", "us": "US", "united kingdom": "GB", "uk": "GB", "india": "IN",\n               "germany": "DE", "france": "FR", "netherlands": "NL", "spain": "ES", "italy": "IT", "canada": "CA",\n               "australia": "AU", "singapore": "SG", "uae": "AE", "united arab emirates": "AE", "brazil": "BR",\n               "mexico": "MX", "south africa": "ZA", "ireland": "IE", "sweden": "SE", "norway": "NO",\n               "denmark": "DK", "finland": "FI", "belgium": "BE", "switzerland": "CH", "austria": "AT",\n               "poland": "PL", "portugal": "PT", "japan": "JP", "new zealand": "NZ", "malaysia": "MY",\n               "indonesia": "ID", "philippines": "PH", "thailand": "TH", "nigeria": "NG", "kenya": "KE",\n               "turkey": "TR", "greece": "GR", "israel": "IL", "saudi arabia": "SA", "egypt": "EG"}\nTLD_ISO = {"uk": "GB", "de": "DE", "fr": "FR", "in": "IN", "nl": "NL", "es": "ES", "it": "IT", "ca": "CA",\n           "au": "AU", "sg": "SG", "ae": "AE", "br": "BR", "mx": "MX", "za": "ZA", "ie": "IE", "se": "SE",\n           "no": "NO", "dk": "DK", "fi": "FI", "be": "BE", "ch": "CH", "at": "AT", "pl": "PL", "pt": "PT",\n           "jp": "JP", "nz": "NZ"}\n\n\ndef region_for(lead):\n    c = (lead.get("country") or "").strip()\n    if len(c) == 2 and c.isalpha():\n        return c.upper()\n    if c.lower() in COUNTRY_ISO:\n        return COUNTRY_ISO[c.lower()]\n    return TLD_ISO.get((lead.get("domain") or "").rsplit(".", 1)[-1])\n\n\ndef _type_name(t):\n    names = {PhoneNumberType.MOBILE: "mobile", PhoneNumberType.FIXED_LINE: "landline",\n             PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline_or_mobile", PhoneNumberType.TOLL_FREE: "toll_free",\n             PhoneNumberType.VOIP: "voip"}\n    return names.get(t, "other")\n\n\ndef find_numbers(text, region):\n    """All VALID phone numbers in text (needs the `phonenumbers` package)."""\n    if phonenumbers is None:\n        return []\n    out = []\n    try:\n        for m in PhoneNumberMatcher(text, region):\n            n = m.number\n            if phonenumbers.is_valid_number(n):\n                out.append({"start": m.start, "end": m.end, "e164": phonenumbers.format_number(n, PhoneNumberFormat.E164),\n                            "region": phonenumbers.region_code_for_number(n),\n                            "line": _type_name(phonenumbers.number_type(n)),\n                            "carrier": _carrier.name_for_number(n, "en") or ""})\n    except Exception:\n        return []\n    return out\n\n\ndef enrich_phone(lead, crawl, finder=None):\n    """Pick the best PUBLIC business number: one printed next to the person\'s name beats a main line."""\n    finder = finder or find_numbers\n    res = {"phone": "", "phone_kind": "", "phone_status": "none", "notes": []}\n    if finder is find_numbers and phonenumbers is None:\n        res["phone_status"] = "unavailable"\n        res["notes"].append("install `phonenumbers` to enable phone extraction")\n        return res\n    region, name = region_for(lead), parse_name(lead.get("contact_name"))\n    best = None\n    for pg in (crawl or {}).get("pages", []):\n        low = pg["text"].lower()\n        for n in finder(pg["text"], region):\n            near = False\n            if name:\n                win = low[max(0, n["start"] - 200): n["end"] + 200]\n                near = name[1] in win and name[0] in win\n            score = 2 if near else 1\n            if region and n.get("region") and n["region"] != region:\n                score -= 0.5  # foreign number on a local company page: probably HQ elsewhere\n            if best is None or score > best[0]:\n                best = (score, n, pg["url"], near)\n    if best:\n        _, n, url, near = best\n        res.update(phone=n["e164"], phone_kind=("direct_listed" if near else "company_main") + "/" + n["line"],\n                   phone_status="verified_public")\n        res["notes"].append("phone from " + url)\n    return res\n\n\n# ---------------------------------------------------------------- orchestration\ndef effective_email_status(lead):\n    email = (lead.get("email") or "").strip()\n    if not email:\n        return "none"\n    if lead.get("email_status"):\n        return lead["email_status"]\n    flags = lead.get("flags") or ""  # legacy rows from before enrichment existed\n    return "unverified" if ("email_not_on_source" in flags or "no_mx_record" in flags) else "verified_public"\n\n\ndef filter_quality(leads, quality):\n    if quality == "verified":\n        return [l for l in leads if effective_email_status(l) in VERIFIED]\n    if quality == "likely":\n        return [l for l in leads if effective_email_status(l) in VERIFIED | LIKELY]\n    return list(leads)\n\n\ndef enrich_lead(lead, crawl, known=(), hunter=None, allow_guess=False, find_phone=True, mx=None,\n                suppressed=(), phone_finder=None):\n    e = enrich_email(lead, crawl, known, hunter, allow_guess, mx, suppressed)\n    p = enrich_phone(lead, crawl, phone_finder) if find_phone else \\\n        {"phone": "", "phone_kind": "", "phone_status": "none", "notes": []}\n    return {**e, **{k: p[k] for k in ("phone", "phone_kind", "phone_status")}, "notes": e["notes"] + p["notes"]}\n\n\ndef enrich_order(order_id, hunter=None, allow_guess=False, find_phone=True, redo=False, suppressed=(),\n                 progress=None, crawl_fn=None, mx=None, phone_finder=None, workers=6):\n    sql = ("SELECT * FROM leads WHERE order_id=? AND COALESCE(stale,0)=0 "\n           "AND status IN (\'verified\',\'auto_verified\',\'needs_review\')")\n    leads = db.q(sql + ("" if redo else " AND enriched_at IS NULL"), (order_id,))\n    crawl_fn = crawl_fn or (lambda d, extra: crawl_domain(d, extra_urls=extra))\n    by_domain = {}\n    for l in leads:\n        by_domain.setdefault(l["domain"], set()).add(l.get("source_url") or "")\n\n    crawls, done = {}, 0\n    with ThreadPoolExecutor(max_workers=workers) as ex:\n        futs = {ex.submit(crawl_fn, d, sorted(u for u in urls if u)): d for d, urls in by_domain.items()}\n        for fut, d in futs.items():\n            try:\n                crawls[d] = fut.result()\n            except Exception:\n                crawls[d] = {"domain": d, "pages": [], "emails": {}}\n            done += 1\n            if progress:\n                progress(0.7 * done / max(len(futs), 1))\n\n    summary = Counter()\n    for i, l in enumerate(leads):\n        known = [(r["contact_name"], r["email"]) for r in db.q(\n            "SELECT contact_name, email FROM leads WHERE domain=? AND email!=\'\' AND id!=?", (l["domain"], l["id"]))]\n        r = enrich_lead(l, crawls.get(l["domain"]), known, hunter, allow_guess, find_phone, mx,\n                        suppressed, phone_finder)\n        flags = ",".join(f for f in (l.get("flags") or "").split(",") if f and not (f == "no_email" and r["email"]))\n        status = l["status"]\n        if (status == "needs_review" and r["email_status"] in {"verified_public", "verified_provider", "inferred_high"}\n                and "name_not_on_source" not in flags and "source_unreachable" not in flags):\n            status = "auto_verified"\n        notes = " | ".join(x for x in [l.get("notes") or "", *r["notes"]] if x)[:500]\n        db.x("UPDATE leads SET email=?, email_status=?, email_source=?, phone=?, phone_kind=?, phone_status=?, "\n             "enriched_at=datetime(\'now\'), notes=?, flags=?, status=? WHERE id=?",\n             (r["email"], r["email_status"], r["email_source"], r["phone"], r["phone_kind"], r["phone_status"],\n              notes, flags, status, l["id"]))\n        summary[r["email_status"]] += 1\n        summary["phone_found"] += 1 if r["phone"] else 0\n        if progress:\n            progress(0.7 + 0.3 * (i + 1) / max(len(leads), 1))\n    summary["processed"] = len(leads)\n    summary["hunter_calls"] = hunter.calls if hunter else 0\n    return dict(summary)\n')

_register('lf_gemini', '"""Gemini helpers: ICP structuring, grounded lead discovery, sales assets."""\nimport json\nimport os\nimport re\n\nfrom google import genai\nfrom google.genai import types\n\nimport lf_verify as verify\n\nDEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")\n\n\ndef _ask(system, user, api_key=None, model=DEFAULT_MODEL, search=False, json_out=False):\n    client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))\n    cfg = {"system_instruction": system, "temperature": 0.4 if search else 0.7}\n    if search:\n        cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]\n    if json_out:\n        cfg["response_mime_type"] = "application/json"\n    resp = client.models.generate_content(\n        model=model, contents=user, config=types.GenerateContentConfig(**cfg))\n    return (resp.text or "").strip()\n\n\ndef extract_json(text):\n    text = re.sub(r"```(?:json)?", "", text).strip()\n    starts = [i for i in (text.find("["), text.find("{")) if i != -1]\n    if not starts:\n        raise ValueError("No JSON in model output")\n    return json.loads(text[min(starts): max(text.rfind("]"), text.rfind("}")) + 1])\n\n\ndef brief_to_icp(brief, api_key=None, model=DEFAULT_MODEL):\n    system = "You turn a client\'s lead-list request into a precise Ideal Customer Profile. Return ONLY JSON."\n    user = f"""Client request:\\n{brief}\\n\nReturn JSON: {{"industry": str, "geography": str, "company_size": str,\n"target_titles": [str], "must_have_signals": [str], "exclusions": [str],\n"search_keywords": [str], "summary": str}}"""\n    return extract_json(_ask(system, user, api_key, model, json_out=True))\n\n\ndef discover_batch(icp, exclude_domains, k=8, api_key=None, model=DEFAULT_MODEL):\n    """Find real companies + one publicly listed decision-maker each, with source URLs."""\n    system = (\n        "You are a careful B2B researcher. Use Google Search. Only report people whose NAME and TITLE "\n        "are publicly displayed on the company\'s own website (team/about/leadership page) or an official "\n        "press page. NEVER guess or infer names or emails. Include an email ONLY if it is literally shown "\n        "on that page (business address); otherwise use an empty string. Return ONLY a JSON array."\n    )\n    user = f"""ICP:\\n{json.dumps(icp, indent=2)}\n\nFind up to {k} DIFFERENT companies that match. Skip these domains: {sorted(exclude_domains)[:150]}\n\nJSON array items:\n{{"company": str, "website": "https://...", "contact_name": str, "title": str,\n  "email": "only if shown publicly else empty", "country": str,\n  "source_url": "exact page URL where the person is listed", "why_fit": str}}"""\n    return extract_json(_ask(system, user, api_key, model, search=True))\n\n\ndef sales_assets(focus, api_key=None, model=DEFAULT_MODEL):\n    system = "You are a B2B direct-response copywriter. Be specific, honest, no hype. Markdown output."\n    user = f"""Business: LeadForge — custom, human-verified B2B lead lists for niche markets. Every contact is\nchecked against its public source, delivered with a source URL, and refreshed monthly on request.\nCompete against Apollo (cheap but stale) and agencies ($2k-15k/mo retainers).\nOur focus niche: {focus}\n\nWrite:\n1. Landing page copy (headline, subhead, 3 benefits, how it works in 4 steps, pricing section, FAQ of 6, CTA)\n2. Offer + guarantee (e.g. free 10-lead sample, replace any bad contact within 14 days)\n3. Three cold emails (different angles, under 110 words each) + 2 follow-ups\n4. Two LinkedIn DM variants (under 60 words)\n5. Six objection handlers (price, "we use Apollo", data accuracy, GDPR, trust, timing)\n6. A 30-day outreach plan (daily volume, channels, metrics)\nEvery email must include an opt-out line and sender identification (CAN-SPAM / GDPR-friendly)."""\n    return _ask(system, user, api_key, model)\n\n\ndef domain_set(leads):\n    return {verify.domain_of(l.get("website", "")) for l in leads if l.get("website")}\n')

# ---------------- app ----------------
import json
import os
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

import lf_gemini as ai
import lf_store as db
import lf_verify as verify
import lf_refresh as refresh
import lf_enrich as enrich

st.set_page_config(page_title="LeadForge", page_icon="🎯", layout="wide")
db.init()

# ---------------- Access gate ----------------
def _password():
    pw = os.getenv("APP_PASSWORD", "")
    if pw:
        return pw
    try:
        return st.secrets.get("APP_PASSWORD", "")
    except Exception:
        return ""


PASSWORD = _password()
if PASSWORD:
    if st.session_state.get("authed") is not True:
        st.title("🎯 LeadForge")
        pw = st.text_input("Password", type="password")
        if st.button("Log in"):
            st.session_state.authed = pw == PASSWORD
            st.rerun()
        st.stop()

# ---------------- Sidebar ----------------
PACKAGES = {  # editable pricing guide (USD)
    "Sample (10 leads)": (10, 0),
    "Starter (100 leads)": (100, 499),
    "Growth (300 leads)": (300, 1199),
    "Scale (1,000 leads)": (1000, 3000),
    "Monthly refresh (200 leads/mo)": (200, 799),
}
ADDONS = {  # per-lead add-ons (USD), adjust to your market
    "Verified-email guarantee (published on the company site, or provider-confirmed)": 1.50,
    "Phone number (public business line; direct when listed with the person)": 1.00,
}
with st.sidebar:
    st.title("🎯 LeadForge")
    api_key = st.text_input("Gemini API key", type="password", value=os.getenv("GEMINI_API_KEY", ""),
                            help="Free at aistudio.google.com/apikey")
    model = st.text_input("Model", value=ai.DEFAULT_MODEL)
    hourly = st.number_input("Your reviewer cost $/hour", 0.0, 200.0, 10.0)
    page = st.radio("Go to", ["Dashboard", "Clients & Orders", "Build List", "Enrichment", "Review Queue",
                              "Deliver", "Monthly Refresh", "Sales Machine", "Compliance", "Sale Readiness"])


def need_key():
    if not api_key:
        st.error("Add your Gemini API key in the sidebar.")
        st.stop()


def suppressed_set():
    return {r["value"] for r in db.q("SELECT value FROM suppression")}


def order_picker(label="Order", statuses=None):
    sql = ("SELECT o.id, o.title, c.name AS client FROM orders o "
           "LEFT JOIN clients c ON c.id=o.client_id ORDER BY o.id DESC")
    orders = db.q(sql)
    if not orders:
        st.info("Create an order first (Clients & Orders).")
        st.stop()
    labels = {f"#{o['id']} · {o['client'] or '—'} · {o['title']}": o["id"] for o in orders}
    return labels[st.selectbox(label, list(labels))]


# ======================================================================
if page == "Dashboard":
    st.header("Dashboard")
    o = db.q("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN paid=1 THEN price_usd END),0) rev FROM orders")[0]
    l = db.q("SELECT COUNT(*) n, SUM(status IN ('verified','auto_verified')) ok, "
             "SUM(status='needs_review') rev FROM leads")[0]
    mrr = db.q("SELECT COALESCE(SUM(price_usd),0) m FROM orders WHERE plan='monthly' AND active=1 AND paid=1")[0]["m"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Orders", o["n"])
    c2.metric("Revenue (paid)", f"${o['rev']:,.0f}")
    c3.metric("MRR", f"${mrr:,.0f}")
    c4.metric("Leads sourced", l["n"] or 0)
    c5.metric("Awaiting review", l["rev"] or 0)
    st.subheader("Pipeline")
    rows = db.q("SELECT o.id, c.name client, o.title, o.status, o.target_count, o.price_usd, o.paid "
                "FROM orders o LEFT JOIN clients c ON c.id=o.client_id ORDER BY o.id DESC")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.download_button("⬇️ Backup database", open(db.DB_PATH, "rb").read(),
                       f"leadforge_backup_{datetime.now():%Y%m%d}.db")
    st.caption("Streamlit Community Cloud storage is temporary. Use a host with a persistent disk "
               "(see README) and download backups regularly.")

# ======================================================================
elif page == "Clients & Orders":
    st.header("Clients & Orders")
    t1, t2, t3 = st.tabs(["New client", "New order", "All orders"])
    with t1:
        with st.form("client"):
            name = st.text_input("Contact name")
            company = st.text_input("Company")
            email = st.text_input("Email")
            if st.form_submit_button("Add client") and name:
                db.x("INSERT INTO clients(name,company,email) VALUES(?,?,?)", (name, company, email))
                st.success("Client added")
    with t2:
        clients = db.q("SELECT * FROM clients ORDER BY id DESC")
        if not clients:
            st.info("Add a client first.")
        else:
            cmap = {f"{c['name']} ({c['company'] or '—'})": c["id"] for c in clients}
            cid = cmap[st.selectbox("Client", list(cmap))]
            pkg = st.selectbox("Package", list(PACKAGES))
            count, price = PACKAGES[pkg]
            title = st.text_input("Order title", value=pkg)
            brief = st.text_area("Client's request in plain words",
                                 placeholder="e.g. Heads of IT at 50-500 employee maritime logistics "
                                             "companies in Europe using legacy ERP")
            c1, c2, c3 = st.columns(3)
            target = c1.number_input("Target leads", 1, 5000, count)
            price_in = c2.number_input("Price USD", 0.0, 100000.0, float(price))
            plan = c3.selectbox("Billing", ["one_time", "monthly"], index=1 if "Monthly" in pkg else 0)
            if st.button("Create order", type="primary") and brief:
                icp = {"brief": brief}
                if api_key:
                    try:
                        icp.update(ai.brief_to_icp(brief, api_key, model))
                    except Exception as e:
                        st.warning(f"Saved brief only (AI structuring failed: {e})")
                db.x("INSERT INTO orders(client_id,title,icp_json,target_count,price_usd,plan,status) "
                     "VALUES(?,?,?,?,?,?,?)", (cid, title, json.dumps(icp), target, price_in, plan, "draft"))
                st.success("Order created. Go to **Build List**.")
                st.json(icp)
    with t3:
        rows = db.q("SELECT o.id, c.name client, o.title, o.plan, o.status, o.price_usd, o.paid, o.active "
                    "FROM orders o LEFT JOIN clients c ON c.id=o.client_id ORDER BY o.id DESC")
        if rows:
            edited = st.data_editor(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                                    disabled=["id", "client", "title", "plan", "status"], key="orders_ed")
            if st.button("Save paid/active flags"):
                for _, r in edited.iterrows():
                    db.x("UPDATE orders SET paid=?, active=?, price_usd=? WHERE id=?",
                         (int(r["paid"]), int(r["active"]), float(r["price_usd"]), int(r["id"])))
                st.success("Saved")

# ======================================================================
elif page == "Build List":
    st.header("Build List")
    oid = order_picker()
    order = db.q("SELECT * FROM orders WHERE id=?", (oid,))[0]
    icp = json.loads(order["icp_json"] or "{}")
    with st.expander("Ideal Customer Profile (editable JSON)", expanded=False):
        icp_txt = st.text_area("ICP", json.dumps(icp, indent=2), height=250)
    have = db.q("SELECT COUNT(*) n FROM leads WHERE order_id=?", (oid,))[0]["n"]
    st.write(f"**{have}/{order['target_count']}** leads sourced so far.")
    run_verify = st.checkbox("Auto-verify against source pages after sourcing", value=True)

    if st.button("🔍 Source leads with AI", type="primary"):
        need_key()
        try:
            icp = json.loads(icp_txt)
        except Exception:
            st.error("ICP JSON is invalid")
            st.stop()
        supp = suppressed_set()
        known = {r["domain"] for r in db.q("SELECT domain FROM leads WHERE order_id=?", (oid,))}
        bar, status_box = st.progress(0.0), st.empty()
        added, tries = 0, 0
        need = order["target_count"] - have
        while added < need and tries < 12:
            tries += 1
            status_box.info(f"Searching... batch {tries} · added {added}/{need}")
            try:
                batch = ai.discover_batch(icp, known, k=8, api_key=api_key, model=model)
            except Exception as e:
                status_box.warning(f"Batch failed ({e}); retrying...")
                time.sleep(5)
                continue
            for b in batch:
                dom = verify.domain_of(b.get("website", ""))
                email = (b.get("email") or "").lower()
                if not dom or dom in known or dom in supp or email in supp or not b.get("contact_name"):
                    continue
                known.add(dom)
                res = verify.check_lead(b) if run_verify else {
                    "confidence": 0, "status": "new", "flags": "", "notes": "", "email": email}
                db.x("INSERT INTO leads(order_id,company,website,domain,contact_name,title,email,country,"
                     "source_url,confidence,status,flags,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (oid, b.get("company"), b.get("website"), dom, b.get("contact_name"), b.get("title"),
                      res["email"], b.get("country"), b.get("source_url"), res["confidence"],
                      res["status"], res["flags"], res["notes"] or b.get("why_fit", "")))
                added += 1
                bar.progress(min(1.0, added / max(need, 1)))
                if added >= need:
                    break
            time.sleep(2)  # be gentle with free-tier rate limits
        db.x("UPDATE orders SET status='review' WHERE id=?", (oid,))
        status_box.success(f"Added {added} leads. Next: **Review Queue**.")

    leads = db.q("SELECT company, contact_name, title, email, confidence, status, flags FROM leads "
                 "WHERE order_id=? ORDER BY confidence DESC", (oid,))
    if leads:
        st.dataframe(pd.DataFrame(leads), use_container_width=True, hide_index=True)

# ======================================================================
elif page == "Enrichment":
    st.header("Enrichment — emails & phone numbers")
    st.caption("Finds the address/number published on each company's own site, infers the company's email format "
               "from published addresses, and (optionally) confirms with Hunter. Every result carries an honest "
               "quality label. It never probes mailboxes and never calls numbers.")
    oid = order_picker()
    c1, c2, c3 = st.columns(3)
    find_phone = c1.checkbox("Find phone numbers", True)
    allow_guess = c2.checkbox("Allow low-confidence guesses", False,
                              help="first.last@domain when nothing else works. Labelled 'guess', never 'verified'.")
    redo = c3.checkbox("Re-enrich already processed leads", False)
    with st.expander("Optional: Hunter.io (paid) for provider-level email verification"):
        hkey = st.text_input("Hunter API key", type="password", value=os.getenv("HUNTER_API_KEY", ""))
        budget = st.number_input("Max Hunter calls this run (1 call = 1 credit)", 0, 5000, 50)
    if enrich.phonenumbers is None and find_phone:
        st.warning("Phone extraction needs the `phonenumbers` package (add it to requirements.txt).")
    pending = db.q("SELECT COUNT(*) n FROM leads WHERE order_id=? AND enriched_at IS NULL AND COALESCE(stale,0)=0 "
                   "AND status IN ('verified','auto_verified','needs_review')", (oid,))[0]["n"]
    st.write(f"**{pending}** lead(s) not yet enriched. Roughly 10-20 seconds per company site.")
    if st.button("✨ Run enrichment", type="primary"):
        hunter = enrich.Hunter(hkey, budget) if hkey and budget else None
        bar = st.progress(0.0)
        with st.spinner("Reading public company pages..."):
            res = enrich.enrich_order(oid, hunter=hunter, allow_guess=allow_guess, find_phone=find_phone,
                                      redo=redo, suppressed=suppressed_set(), progress=bar.progress)
        st.success(f"Processed {res.get('processed', 0)} · phones found {res.get('phone_found', 0)} · "
                   f"Hunter calls {res.get('hunter_calls', 0)}")
        if hunter and hunter.last_error:
            st.warning(hunter.last_error)

    rows = db.q("SELECT * FROM leads WHERE order_id=? AND COALESCE(stale,0)=0 AND status!='rejected'", (oid,))
    if rows:
        for r_ in rows:
            r_["email_quality"] = enrich.effective_email_status(r_)
        n = len(rows)
        counts = pd.Series([r_["email_quality"] for r_ in rows]).value_counts()
        verified = sum(counts.get(k, 0) for k in enrich.VERIFIED)
        likely = sum(counts.get(k, 0) for k in enrich.LIKELY)
        a, b, c, d_ = st.columns(4)
        a.metric("Verified emails", f"{verified} ({verified / n * 100:.0f}%)")
        b.metric("Likely emails", f"{likely} ({likely / n * 100:.0f}%)")
        c.metric("No email", int(counts.get("none", 0)))
        d_.metric("Phones (verified public)", sum(1 for r_ in rows if r_.get("phone_status") == "verified_public"))
        st.dataframe(pd.DataFrame(rows)[["company", "contact_name", "email", "email_quality", "email_source",
                                         "phone", "phone_kind"]], use_container_width=True, hide_index=True)
        st.caption("Leads whose email is verified or inferred_high move to auto_verified; approve them in Review Queue. "
                   "Sell 'verified email' only for the verified statuses.")

# ======================================================================
elif page == "Review Queue":
    st.header("Human Review Queue")
    st.caption("This is your edge: a person confirms every lead before delivery. "
               "Open the source link, confirm the person/title/email, then set the status.")
    oid = order_picker()
    only_pending = st.checkbox("Only show needs_review / new", value=True)
    sql = "SELECT id, company, contact_name, title, email, email_status, phone, source_url, confidence, flags, " \
          "notes, status " \
          "FROM leads WHERE order_id=?"
    if only_pending:
        sql += " AND status IN ('needs_review','new','auto_verified')"
    leads = db.q(sql + " ORDER BY confidence DESC", (oid,))
    if not leads:
        st.success("Nothing to review.")
    else:
        df = pd.DataFrame(leads)
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True, height=520,
            disabled=["id", "company", "confidence", "flags", "source_url", "email_status"],
            column_config={
                "status": st.column_config.SelectboxColumn(
                    options=["new", "needs_review", "auto_verified", "verified", "rejected"]),
                "source_url": st.column_config.LinkColumn("source"),
            }, key=f"review_{oid}")
        minutes = st.number_input("Minutes spent on this review session (for margin tracking)", 0.0, 600.0, 0.0)
        if st.button("💾 Save review", type="primary"):
            def _s(v):
                return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
            orig = {l["id"]: l for l in leads}
            for _, r in edited.iterrows():
                email = _s(r["email"]).strip().lower()
                if email and not verify.valid_syntax(email):
                    continue
                before = _s(orig[int(r["id"])]["email"]).lower()
                estatus = _s(r["email_status"]) or None
                if email and email != before:
                    estatus = "verified_manual"  # a human confirmed or typed it
                db.x("UPDATE leads SET contact_name=?, title=?, email=?, email_status=?, phone=?, notes=?, "
                     "status=? WHERE id=?",
                     (_s(r["contact_name"]), _s(r["title"]), email, estatus, _s(r["phone"]), _s(r["notes"]),
                      r["status"], int(r["id"])))
            db.x("UPDATE orders SET review_minutes=review_minutes+? WHERE id=?", (minutes, oid))
            st.success("Saved")
        if st.button("✅ Approve all auto_verified"):
            db.x("UPDATE leads SET status='verified' WHERE order_id=? AND status='auto_verified'", (oid,))
            st.rerun()

# ======================================================================
elif page == "Deliver":
    st.header("Deliver")
    oid = order_picker()
    order = db.q("SELECT o.*, c.name client FROM orders o LEFT JOIN clients c ON c.id=o.client_id "
                 "WHERE o.id=?", (oid,))[0]
    supp = suppressed_set()
    leads = [l for l in db.q("SELECT * FROM leads WHERE order_id=? AND status='verified'", (oid,))
             if (l["email"] or "") not in supp and l["domain"] not in supp]
    quality = st.selectbox("Email quality to deliver", ["Verified emails only", "Verified + likely", "All leads"],
                           help="Verified = published on the company site, provider-confirmed or human-confirmed. "
                                "Likely = proven company format or accept-all server. Guesses are never 'verified'.")
    for l in leads:
        l["email_status"] = enrich.effective_email_status(l)
        if l.get("phone_status") != "verified_public":
            l["phone"], l["phone_kind"] = "", ""
    leads = enrich.filter_quality(leads, {"Verified emails only": "verified",
                                          "Verified + likely": "likely"}.get(quality, "all"))
    allrev = db.q("SELECT status, COUNT(*) n FROM leads WHERE order_id=? GROUP BY status", (oid,))
    st.write({r["status"]: r["n"] for r in allrev})
    st.metric("Deliverable (verified, not suppressed)", f"{len(leads)} / {order['target_count']}")
    if leads:
        cols = ["company", "website", "contact_name", "title", "email", "email_status", "phone", "phone_kind",
                "country", "source_url", "confidence"]
        df = pd.DataFrame(leads)[cols]
        total_rev = sum(r["n"] for r in allrev if r["status"] in ("verified", "rejected"))
        rejected = next((r["n"] for r in allrev if r["status"] == "rejected"), 0)
        report = pd.DataFrame({
            "Metric": ["Leads delivered", "Reviewed by a human", "Rejected during QA", "Avg confidence",
                       "Delivery date", "Replacement guarantee"],
            "Value": [len(df), total_rev, rejected, round(df["confidence"].mean(), 1),
                      f"{datetime.now():%Y-%m-%d}", "Bad contact replaced free within 14 days"]})
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, sheet_name="Leads", index=False)
            report.to_excel(w, sheet_name="Verification report", index=False)
            df["email_status"].value_counts().rename_axis("Email status").reset_index(name="Leads").to_excel(
                w, sheet_name="Email quality", index=False)
        st.download_button("⬇️ Download XLSX (with report)", buf.getvalue(),
                           f"leads_order_{oid}.xlsx")
        st.download_button("⬇️ Download CSV", df.to_csv(index=False), f"leads_order_{oid}.csv")
        extra = st.number_input("Extra direct costs for this order (API, tools) $", 0.0, 10000.0,
                                float(order["cost_usd"]))
        if st.button("Mark order delivered"):
            db.x("UPDATE orders SET status='delivered', delivered_at=datetime('now'), cost_usd=? WHERE id=?",
                 (extra, oid))
            st.success("Delivered. Remember to send the privacy notice + opt-out contact (see Compliance).")
        margin = order["price_usd"] - extra - order["review_minutes"] / 60 * hourly
        st.metric("Order gross margin", f"${margin:,.0f}",
                  f"{(margin / order['price_usd'] * 100) if order['price_usd'] else 0:.0f}%")
        st.dataframe(df, use_container_width=True, hide_index=True)

# ======================================================================
elif page == "Monthly Refresh":
    st.header("Monthly Refresh — your recurring-revenue engine")
    st.caption("Re-check every delivered lead against its public source, flag contacts that went stale, "
               "source replacements, and send the client a report. Run it monthly for each subscription.")

    subs = db.q("SELECT o.id, c.name AS client, o.title, o.price_usd, o.last_refresh, "
                "CAST(julianday('now') - julianday(COALESCE(o.last_refresh, o.delivered_at, o.created)) AS INTEGER) "
                "AS days_since FROM orders o LEFT JOIN clients c ON c.id=o.client_id "
                "WHERE o.plan='monthly' AND o.active=1 ORDER BY days_since DESC")
    if subs:
        sdf = pd.DataFrame(subs)
        sdf["due"] = sdf["days_since"] >= 30
        st.subheader("Subscriptions")
        st.dataframe(sdf, use_container_width=True, hide_index=True)
    else:
        st.info("No active monthly orders yet. Create an order with Billing = monthly to use refreshes.")

    d = refresh.decay_stats()
    if d["checked"]:
        st.metric("Observed data decay across your refreshes", f"{d['decay_pct']}% stale",
                  f"{d['stale']} of {d['checked']} contacts checked, {d['runs']} run(s)")
        st.caption("Use this as a real proof point in sales once you have 100+ contacts checked. "
                   "Small samples are noisy, so don't quote it before then.")

    st.divider()
    oid = order_picker("Order to refresh")
    order = db.q("SELECT * FROM orders WHERE id=?", (oid,))[0]
    icp = json.loads(order["icp_json"] or "{}")
    live = db.q("SELECT COUNT(*) n FROM leads WHERE order_id=? AND status='verified' AND COALESCE(stale,0)=0",
                (oid,))[0]["n"]
    st.write(f"**{live}** verified, non-stale leads will be re-checked. "
             f"Last refresh: {order.get('last_refresh') or 'never'}.")

    st.subheader("Step 1 — Re-verify delivered leads")
    if st.button("🔄 Run re-verification", type="primary"):
        bar = st.progress(0.0)
        with st.spinner("Re-checking source pages (about 1 second per unique page)..."):
            res = refresh.run_refresh(oid, progress=bar.progress)
        st.success(f"Checked {res['checked']}: {res['ok']} ok · {res['stale']} stale · "
                   f"{res['unconfirmed']} unconfirmed · {res['manual']} manual")

    last = db.q("SELECT * FROM refreshes WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,))
    if last:
        r = last[0]
        a, b, c, e = st.columns(4)
        a.metric("Checked", r["checked"])
        b.metric("Still valid", r["ok"])
        c.metric("Went stale", r["stale"])
        e.metric("Couldn't auto-check", r["unconfirmed"] + r["manual"])
        stale_rows = db.q("SELECT company, contact_name, title, stale_reason FROM leads "
                          "WHERE order_id=? AND stale=1 AND last_checked >= ?", (oid, r["run_at"]))
        if stale_rows:
            st.dataframe(pd.DataFrame(stale_rows), use_container_width=True, hide_index=True)
        st.caption("'Stale' means the name/title no longer appears on the source page, or the email domain "
                   "stopped accepting mail. A page redesign can cause false alarms, so spot-check a few "
                   "before sending. Unreachable pages are NOT marked stale.")

    st.subheader("Step 2 — Source replacements")
    todo = len(refresh.unreplaced_stale(oid))
    st.write(f"{todo} stale contact(s) still need a replacement.")
    if st.button("🔍 Find replacements with AI") and todo:
        need_key()
        bar2 = st.progress(0.0)
        with st.spinner("Searching for replacement contacts..."):
            n = refresh.replace_stale(
                oid, icp, lambda i, known, k: ai.discover_batch(i, known, k, api_key=api_key, model=model),
                suppressed=suppressed_set(), progress=bar2.progress)
        st.success(f"Added {n} replacement(s). Approve them in **Review Queue** before reporting.")

    st.subheader("Step 3 — Client report")
    st.caption("Approve replacements in the Review Queue first; only verified replacements go in the report.")
    if last and st.button("📄 Build report"):
        text, xlsx = refresh.build_report(oid)
        st.session_state[f"rep_{oid}"] = (text, xlsx)
    if f"rep_{oid}" in st.session_state:
        text, xlsx = st.session_state[f"rep_{oid}"]
        st.text_area("Message to send the client", text, height=300)
        st.download_button("⬇️ Download refresh workbook (XLSX)", xlsx, f"refresh_order_{oid}.xlsx")
        if st.button("✅ Mark refresh delivered"):
            st.success("Done. Invoice the next month and keep going.")

# ======================================================================
elif page == "Sales Machine":
    st.header("Sales Machine — get your first 10 clients")
    focus = st.text_input("Your focus niche (specialize to win)",
                          placeholder="e.g. lead lists for B2B SaaS companies selling to healthcare IT")
    if st.button("Generate landing page, emails, DMs, objection handlers", type="primary"):
        need_key()
        with st.spinner("Writing..."):
            st.session_state.sales = ai.sales_assets(focus or "niche B2B markets", api_key, model)
    if st.session_state.get("sales"):
        st.markdown(st.session_state.sales)
        st.download_button("⬇️ Download (Markdown)", st.session_state.sales, "sales_assets.md")
    st.subheader("Pricing guide")
    st.table(pd.DataFrame([{"Package": k, "Leads": v[0], "Price $": v[1],
                            "$/lead": round(v[1] / v[0], 2) if v[1] else 0} for k, v in PACKAGES.items()]))
    st.table(pd.DataFrame([{"Add-on": k, "Price per lead $": v} for k, v in ADDONS.items()]))
    st.markdown("""**Playbook:** offer a free 10-lead sample (create a Sample order) → show the source URL for every
contact → upsell Starter → convert to **Monthly refresh** (recurring revenue is what buyers pay a premium for).""")

# ======================================================================
elif page == "Compliance":
    st.header("Compliance & Suppression")
    st.warning("Not legal advice. Have a lawyer review your Terms, Privacy Policy and DPA before launch.")
    st.markdown("""
**Built-in safeguards**
- Business contact data only, taken from public company pages. **No LinkedIn scraping, no private/personal emails.**
- Every lead stores its **source URL** (provable origin).
- **Suppression list** blocks opted-out people/domains from every future order and delivery.
- Humans confirm each lead; AI never invents contacts (names must appear on the source page).

**Your checklist**
1. Publish a Privacy Policy + opt-out contact; honour removal requests within 30 days (add them below).
2. EU/UK: rely on *legitimate interest* for B2B, keep a documented assessment; avoid sole traders/personal emails.
3. Canada (CASL) and Germany are strict on cold email; Tell clients to include sender ID + unsubscribe (CAN-SPAM).
4. Sign a Data Processing Agreement with clients; don't resell the same list to competitors without disclosure.
5. India (DPDP Act), California (CCPA): keep a deletion process.
6. Phones are business lines published on company websites only. Cold calling has its own rules: US (TCPA + Do Not
   Call, extra care with mobiles), UK (TPS/CTPS), India (NDNC), EU member-state rules. Tell clients to screen numbers
   against the relevant do-not-call lists before dialling.
7. Emails marked *inferred* or *guess* are not confirmed mailboxes. Never describe them as verified.
""")
    new = st.text_area("Add to suppression list (emails or domains, one per line)")
    if st.button("Add") and new.strip():
        for v in new.splitlines():
            if v.strip():
                try:
                    db.x("INSERT INTO suppression(value,reason) VALUES(?,?)", (v.strip().lower(), "opt-out"))
                except Exception:
                    pass
        st.success("Added")
    st.dataframe(pd.DataFrame(db.q("SELECT value, reason, created FROM suppression ORDER BY id DESC")),
                 use_container_width=True, hide_index=True)

# ======================================================================
elif page == "Sale Readiness":
    st.header("Sale Readiness — what buyers will ask for")
    rev = db.q("SELECT COALESCE(SUM(price_usd),0) r, COALESCE(SUM(cost_usd),0) c, "
               "COALESCE(SUM(review_minutes),0) m FROM orders WHERE paid=1")[0]
    labor = rev["m"] / 60 * hourly
    profit = rev["r"] - rev["c"] - labor
    mrr = db.q("SELECT COALESCE(SUM(price_usd),0) m FROM orders WHERE plan='monthly' AND active=1 AND paid=1")[0]["m"]
    rec_share = (mrr * 12 / rev["r"] * 100) if rev["r"] else 0
    cl = db.q("SELECT c.id, SUM(o.price_usd) r, COUNT(o.id) n FROM clients c JOIN orders o ON o.client_id=c.id "
              "WHERE o.paid=1 GROUP BY c.id")
    n_clients = len(cl)
    repeat = sum(1 for c in cl if c["n"] > 1) / n_clients * 100 if n_clients else 0
    top = max((c["r"] for c in cl), default=0) / rev["r"] * 100 if rev["r"] else 0
    qa = db.q("SELECT SUM(status='verified') v, SUM(status='rejected') r FROM leads")[0]
    acc = (qa["v"] or 0) / ((qa["v"] or 0) + (qa["r"] or 0)) * 100 if (qa["v"] or 0) + (qa["r"] or 0) else 0
    tat = db.q("SELECT AVG(julianday(delivered_at)-julianday(created)) d FROM orders WHERE delivered_at IS NOT NULL")[0]["d"]

    a, b, c, d = st.columns(4)
    a.metric("Revenue (paid)", f"${rev['r']:,.0f}")
    b.metric("Net profit (est.)", f"${profit:,.0f}")
    c.metric("MRR / ARR", f"${mrr:,.0f} / ${mrr * 12:,.0f}")
    d.metric("Clients", n_clients)
    e, f, g, h = st.columns(4)
    e.metric("Recurring share", f"{rec_share:.0f}%")
    f.metric("Repeat clients", f"{repeat:.0f}%")
    g.metric("Top-client concentration", f"{top:.0f}%")
    h.metric("QA pass rate", f"{acc:.0f}%")
    st.metric("Avg turnaround (days)", f"{tat:.1f}" if tat else "—")

    st.subheader("Rough valuation guide")
    st.write(f"Small service/data businesses commonly sell for ~2–3.5× annual profit. If you ran at today's pace "
             f"for a year, profit ≈ **${profit * 12:,.0f}** → indicative range "
             f"**${profit * 12 * 2:,.0f} – ${profit * 12 * 3.5:,.0f}**. "
             f"(Based on lifetime figures annualised crudely; real valuations depend on trailing 12 months, "
             f"recurring revenue, concentration and documentation.)")
    st.markdown("""**To raise the multiple:** push recurring share above 50%, no client above 25% of revenue,
documented SOPs (see `docs/SOP.md`), a trained reviewer who isn't you, QA pass rate >95%,
and 12+ months of clean records. List on Acquire.com / Flippa / Empire Flippers when ready.""")
    summary = (f"# LeadForge data-room summary ({datetime.now():%Y-%m-%d})\n\n"
               f"- Revenue: ${rev['r']:,.0f}\n- Net profit est.: ${profit:,.0f}\n- MRR: ${mrr:,.0f}\n"
               f"- Clients: {n_clients} | Repeat: {repeat:.0f}% | Top client share: {top:.0f}%\n"
               f"- QA pass rate: {acc:.0f}% | Avg turnaround: {tat or 0:.1f} days\n")
    st.download_button("⬇️ Data-room summary", summary, "data_room_summary.md")
