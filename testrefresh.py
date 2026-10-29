import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import lf_store as db  # noqa: E402
import lf_refresh as refresh  # noqa: E402

PAD = " filler text about the company and its products " * 10


def setup_db():
    db.DB_PATH = tempfile.mktemp(suffix=".db")
    db.init()
    cid = db.x("INSERT INTO clients(name) VALUES('Acme Buyer')")
    oid = db.x("INSERT INTO orders(client_id,title,plan,paid,status) VALUES(?,?,?,?,?)",
               (cid, "Maritime IT", "monthly", 1, "delivered"))
    return oid


def add(oid, name, title, email, url, flags="", status="verified"):
    return db.x("INSERT INTO leads(order_id,company,website,domain,contact_name,title,email,source_url,"
                "status,flags) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (oid, name + " Co", f"https://{name}.com", f"{name}.com", name.title() + " Person",
                 title, email, url, status, flags))


def test_all_states():
    oid = setup_db()
    add(oid, "alpha", "CTO", "a@alpha.com", "u/ok")
    add(oid, "bravo", "CTO", "b@bravo.com", "u/gone")
    add(oid, "charlie", "CTO", "c@charlie.com", "u/title")
    add(oid, "delta", "CTO", "d@delta.com", "u/dead")
    add(oid, "echo", "CTO", "e@echo.com", "u/down")
    add(oid, "foxtrot", "CTO", "f@foxtrot.com", "u/manual", flags="name_not_on_source")
    pages = {
        "u/ok": "alpha person cto " + PAD,
        "u/gone": "some other team member " + PAD,
        "u/title": "charlie person vp engineering " + PAD,
        "u/dead": "delta person cto " + PAD,
        "u/down": None,
        "u/manual": "x" * 300,
    }
    s = refresh.run_refresh(oid, fetch=lambda u: pages[u], mx=lambda d: d != "delta.com", pause=0)
    assert (s["checked"], s["ok"], s["stale"], s["unconfirmed"], s["manual"]) == (6, 1, 3, 1, 1)
    states = {l["domain"]: (l["status"], l["stale_reason"]) for l in db.q("SELECT * FROM leads")}
    assert states["bravo.com"][0] == "stale" and "no longer appears" in states["bravo.com"][1]
    assert states["charlie.com"][0] == "stale" and "title" in states["charlie.com"][1]
    assert states["delta.com"][0] == "stale" and "email" in states["delta.com"][1]
    assert states["echo.com"][0] == "verified"  # unreachable is NOT marked stale


def test_replacement_and_report():
    oid = setup_db()
    add(oid, "alpha", "CTO", "a@alpha.com", "u/ok")
    add(oid, "bravo", "CTO", "b@bravo.com", "u/gone")
    pages = {"u/ok": "alpha person cto a@alpha.com " + PAD, "u/gone": "nobody here " + PAD}
    refresh.run_refresh(oid, fetch=lambda u: pages[u], mx=lambda d: True, pause=0)
    assert len(refresh.unreplaced_stale(oid)) == 1

    def discover(icp, known, k):
        assert "bravo.com" in known  # never re-suggest the stale company
        return [{"company": "New Co", "website": "https://newco.com", "contact_name": "New Person",
                 "title": "CTO", "email": "", "source_url": "u/new", "country": "DE"}]

    import lf_verify
    lf_verify.fetch_text = lambda u: "new person cto " + PAD  # replacement page for auto-check
    n = refresh.replace_stale(oid, {}, discover, pause=0)
    assert n == 1 and len(refresh.unreplaced_stale(oid)) == 0
    new = db.q("SELECT * FROM leads WHERE domain='newco.com'")[0]
    assert new["replaces_id"] and new["status"] == "needs_review"  # no email -> human must check
    db.x("UPDATE leads SET status='verified' WHERE id=?", (new["id"],))
    text, xlsx = refresh.build_report(oid)
    assert "went stale: 1" in text.lower().replace("•", "").replace("—", "") or "Went stale: 1" in text
    assert "Verified replacements attached: 1" in text and len(xlsx) > 1000
    assert refresh.decay_stats()["decay_pct"] == 50.0


def test_migration_on_old_db():
    import sqlite3
    path = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE leads(id INTEGER PRIMARY KEY, order_id INTEGER, status TEXT)")
    c.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY)")
    c.commit(); c.close()
    db.DB_PATH = path
    db.init(); db.init()  # idempotent
    cols = [r["name"] for r in db.q("PRAGMA table_info(leads)")]
    assert "stale" in cols and "replaces_id" in cols
