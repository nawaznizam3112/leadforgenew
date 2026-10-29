"""Monthly refresh: re-verify delivered leads, flag stale ones, source replacements, build client report."""
import re
import time
from io import BytesIO

import lf_store as db
import lf_verify as verify

STALE_STATES = ("departed", "email_dead", "role_changed")


def _tokens(name):
    return [t for t in re.findall(r"[^\W\d_]+", (name or "").lower()) if len(t) > 1]


def recheck_lead(lead, get_page, mx=None):
    """Return (state, reason). States: ok, departed, email_dead, role_changed, unconfirmed, manual."""
    mx = mx or verify.has_mx
    flags = lead.get("flags") or ""
    if "name_not_on_source" in flags or "source_unreachable" in flags:
        return "manual", "originally verified by hand (source not machine-readable); re-check manually"
    page = get_page(lead.get("source_url") or "")
    if page is None:
        return "unconfirmed", "source page unreachable this time"
    if len(page) < 200:
        return "unconfirmed", "source page returned too little text (blocked or JS-rendered)"
    tokens = _tokens(lead.get("contact_name"))
    if not tokens or tokens[0] not in page or tokens[-1] not in page:
        return "departed", "name no longer appears on the source page"
    email = (lead.get("email") or "").lower()
    if email and not mx(verify.domain_of(email)):
        return "email_dead", "email domain no longer accepts mail"
    title = (lead.get("title") or "").strip().lower()
    if title and "title_not_on_source" not in flags and title not in page:
        return "role_changed", f"title '{lead.get('title')}' no longer appears on the source page"
    return "ok", ""


def run_refresh(order_id, fetch=None, mx=None, progress=None, pause=0.3):
    """Re-check every verified, non-stale lead of an order. Updates DB, returns summary dict."""
    fetch = fetch or verify.fetch_text
    leads = db.q("SELECT * FROM leads WHERE order_id=? AND status='verified' AND COALESCE(stale,0)=0",
                 (order_id,))
    run_id = db.x("INSERT INTO refreshes(order_id) VALUES(?)", (order_id,))
    cache = {}

    def get_page(url):
        if url in cache:
            return cache[url]
        page = fetch(url) or fetch(url)  # one retry for flaky pages
        cache[url] = page
        time.sleep(pause)
        return page

    counts = {"ok": 0, "stale": 0, "unconfirmed": 0, "manual": 0}
    for i, lead in enumerate(leads):
        state, reason = recheck_lead(lead, get_page, mx)
        if state in STALE_STATES:
            db.x("UPDATE leads SET stale=1, status='stale', stale_reason=?, last_checked=datetime('now') "
                 "WHERE id=?", (reason, lead["id"]))
            counts["stale"] += 1
        else:
            db.x("UPDATE leads SET last_checked=datetime('now'), stale_reason=? WHERE id=?",
                 (reason if state != "ok" else "", lead["id"]))
            counts[state] += 1
        if progress:
            progress((i + 1) / max(len(leads), 1))
    db.x("UPDATE refreshes SET checked=?, ok=?, stale=?, unconfirmed=?, manual=? WHERE id=?",
         (len(leads), counts["ok"], counts["stale"], counts["unconfirmed"], counts["manual"], run_id))
    db.x("UPDATE orders SET last_refresh=datetime('now') WHERE id=?", (order_id,))
    return {"run_id": run_id, "checked": len(leads), **counts}


def unreplaced_stale(order_id):
    return db.q("SELECT id FROM leads WHERE order_id=? AND stale=1 AND id NOT IN "
                "(SELECT replaces_id FROM leads WHERE replaces_id IS NOT NULL) ORDER BY id", (order_id,))


def replace_stale(order_id, icp, discover, suppressed=(), max_batches=8, pause=2, progress=None):
    """Source + auto-check new leads for each stale one. `discover(icp, known_domains, k)` -> list of dicts.
    Replacements land in the review queue (auto_verified / needs_review); a human still approves them."""
    todo = unreplaced_stale(order_id)
    known = {r["domain"] for r in db.q("SELECT domain FROM leads WHERE order_id=?", (order_id,))}
    added = 0
    for batch_no in range(max_batches):
        if not todo:
            break
        try:
            batch = discover(icp, known, 8)
        except Exception:
            time.sleep(5)
            continue
        for b in batch:
            if not todo:
                break
            dom = verify.domain_of(b.get("website", ""))
            email = (b.get("email") or "").lower()
            if not dom or dom in known or dom in suppressed or email in suppressed or not b.get("contact_name"):
                continue
            known.add(dom)
            res = verify.check_lead(b)
            old = todo.pop(0)
            db.x("INSERT INTO leads(order_id,company,website,domain,contact_name,title,email,country,"
                 "source_url,confidence,status,flags,notes,replaces_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (order_id, b.get("company"), b.get("website"), dom, b.get("contact_name"), b.get("title"),
                  res["email"], b.get("country"), b.get("source_url"), res["confidence"], res["status"],
                  res["flags"], res["notes"] or b.get("why_fit", ""), old["id"]))
            added += 1
        if progress:
            progress(min(1.0, (batch_no + 1) / max_batches))
        time.sleep(pause)
    if added:
        db.x("UPDATE refreshes SET replaced=replaced+? WHERE id=(SELECT MAX(id) FROM refreshes WHERE order_id=?)",
             (added, order_id))
    return added


def decay_stats():
    r = db.q("SELECT COALESCE(SUM(checked),0) c, COALESCE(SUM(stale),0) s, COUNT(*) n FROM refreshes")[0]
    return {"checked": r["c"], "stale": r["s"], "runs": r["n"],
            "decay_pct": round(r["s"] / r["c"] * 100, 1) if r["c"] else 0.0}


def build_report(order_id):
    """Return (client_message_text, xlsx_bytes) for the latest refresh of an order."""
    import pandas as pd
    order = db.q("SELECT o.*, c.name client FROM orders o LEFT JOIN clients c ON c.id=o.client_id "
                 "WHERE o.id=?", (order_id,))[0]
    run = db.q("SELECT * FROM refreshes WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,))
    if not run:
        raise ValueError("Run a refresh first")
    run = run[0]
    cols = "company, contact_name, title, email, country, source_url"
    stale = db.q(f"SELECT id, {cols}, stale_reason FROM leads WHERE order_id=? AND stale=1 "
                 "AND last_checked >= ?", (order_id, run["run_at"]))
    ids = [s["id"] for s in stale]
    repl = db.q(f"SELECT {cols}, confidence, replaces_id FROM leads WHERE order_id=? AND status='verified' "
                "AND replaces_id IS NOT NULL", (order_id,))
    repl = [r for r in repl if r["replaces_id"] in ids]
    pending = db.q("SELECT COUNT(*) n FROM leads WHERE order_id=? AND replaces_id IS NOT NULL "
                   "AND status IN ('new','needs_review','auto_verified')", (order_id,))[0]["n"]
    valid = db.q(f"SELECT {cols}, last_checked FROM leads WHERE order_id=? AND status='verified' "
                 "AND COALESCE(stale,0)=0 AND replaces_id IS NULL", (order_id,))

    pct = round(run["stale"] / run["checked"] * 100) if run["checked"] else 0
    lines = [f"Hi {order['client'] or 'there'},", "",
             f"Your monthly refresh for \"{order['title']}\" is ready. We re-checked {run['checked']} contacts "
             f"against their original public sources.", "",
             f"• Still valid: {run['ok']}",
             f"• Went stale: {run['stale']} ({pct}%) — the person or title no longer matches the source page, "
             f"or the email domain stopped accepting mail",
             f"• Verified replacements attached: {len(repl)}"]
    if pending:
        lines.append(f"• Replacements still being verified by our team: {pending} (we'll send them shortly)")
    if run["unconfirmed"] + run["manual"]:
        lines.append(f"• Couldn't be re-checked automatically (source unreachable or manually verified): "
                     f"{run['unconfirmed'] + run['manual']} — kept in your list, flagged for a manual check")
    lines += ["", "The attached workbook lists every stale contact with the reason and its replacement.",
              "Reply to this email if you'd like any replaced contact reviewed again.", "", "— LeadForge"]
    text = "\n".join(lines)

    summary = pd.DataFrame({"Metric": ["Contacts re-checked", "Still valid", "Went stale", "Stale %",
                                       "Verified replacements", "Replacements pending", "Not auto-checkable"],
                            "Value": [run["checked"], run["ok"], run["stale"], f"{pct}%", len(repl),
                                      pending, run["unconfirmed"] + run["manual"]]})
    stale_df = pd.DataFrame(stale).drop(columns=["id"], errors="ignore")
    repl_df = pd.DataFrame(repl).drop(columns=["replaces_id"], errors="ignore")
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="Summary", index=False)
        stale_df.to_excel(w, sheet_name="Stale contacts", index=False)
        repl_df.to_excel(w, sheet_name="Replacements", index=False)
        pd.DataFrame(valid).to_excel(w, sheet_name="Still valid", index=False)
    return text, buf.getvalue()
