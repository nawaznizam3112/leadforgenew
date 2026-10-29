import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import lf_store as db  # noqa: E402
import lf_enrich as en  # noqa: E402

PAD = " company information and products " * 12
SITE = {
    "https://acme.com": "<html><body>Welcome " + PAD + "</body></html>",
    "https://acme.com/contact": "<html><a href='mailto:Anna.Berg@acme.com'>mail</a> Call +49 30 1234567 "
                                "reach Mark [at] acme [dot] com or tom.lee@acme.com or info@acme.com " + PAD + "</html>",
    "https://acme.com/team": "<html>Jane Doe, Head of IT, Direct +49 30 7654321. " + PAD + "</html>",
}


def fetch(url):
    return SITE.get(url)


def crawl(domain="acme.com", extra=()):
    return en.crawl_domain(domain, fetch_html=fetch, extra_urls=extra, pause=0)


class FakeHunter:
    def __init__(self, verify_status="valid", find_result=None):
        self.vs, self.fr, self.calls, self.last_error = verify_status, find_result, 0, ""

    def verify(self, email):
        self.calls += 1
        return self.vs

    def find(self, domain, f, l):
        self.calls += 1
        return self.fr


def lead(name="Jane Doe", email="", **kw):
    base = {"contact_name": name, "email": email, "domain": "acme.com", "website": "https://acme.com",
            "source_url": "https://acme.com/team", "country": "Germany", "flags": ""}
    base.update(kw)
    return base


MX = lambda d: True  # noqa: E731


def test_names_and_patterns():
    assert en.parse_name("Dr. José Núñez-Pérez Jr.") == ("jose", "nunezperez")
    assert en.parse_name("Cher") is None
    assert en.match_pattern("jdoe", "jane", "doe") == "flast"
    assert en.render("{first}.{last}", "jane", "doe") == "jane.doe"
    assert en.render("{f}{last}", "jane", "doe") == "jdoe"


def test_crawl_harvests_and_deobfuscates():
    c = crawl(extra=["https://acme.com/team"])
    assert {"anna.berg@acme.com", "mark@acme.com", "tom.lee@acme.com", "info@acme.com"} <= set(c["emails"])
    assert len(c["pages"]) == 3


def test_dead_site_stops_quickly():
    calls = []
    c = en.crawl_domain("dead.com", fetch_html=lambda u: calls.append(u) or None, pause=0)
    assert c["pages"] == [] and len(calls) == 2  # https + www only


def test_inference_medium_then_high():
    key, w, ev = en.infer_pattern(["anna.berg", "tom.lee", "info"], [])
    assert key == "first.last" and w == 2  # two shape votes; role address ignored
    key, w, _ = en.infer_pattern(["anna.berg"], [("Tom Lee", "tom.lee@acme.com")])
    assert key == "first.last" and w == 3


def test_email_inferred_never_verified():
    r = en.enrich_email(lead(), crawl(), mx=MX)
    assert r["email"] == "jane.doe@acme.com" and r["email_status"] == "inferred_medium"
    known = [("Tom Lee", "tom.lee@acme.com")]
    r = en.enrich_email(lead(), crawl(), known=known, mx=MX)
    assert r["email_status"] == "inferred_high"


def test_no_mx_blocks_inference():
    r = en.enrich_email(lead(), crawl(), mx=lambda d: False)
    assert r["email"] == "" and r["email_status"] == "none"


def test_public_email_match():
    r = en.enrich_email(lead("Anna Berg"), crawl(), mx=MX)
    assert r["email"] == "anna.berg@acme.com" and r["email_status"] == "verified_public"


def test_existing_email_found_on_page_is_verified():
    r = en.enrich_email(lead("Tom Lee", "tom.lee@acme.com"), crawl(), mx=MX)
    assert r["email_status"] == "verified_public"
    r = en.enrich_email(lead("X Y", "x.y@acme.com"), crawl(), mx=MX)
    assert r["email_status"] == "unverified"


def test_guess_is_opt_in_and_labelled():
    c = {"emails": {}, "pages": []}
    assert en.enrich_email(lead(), c, mx=MX)["email"] == ""
    r = en.enrich_email(lead(), c, allow_guess=True, mx=MX)
    assert r["email_status"] == "guess"
    assert "guess" not in en.VERIFIED | en.LIKELY


def test_hunter_paths():
    c = {"emails": {}, "pages": []}
    h = FakeHunter(find_result={"email": "jane@acme.com", "score": 90, "status": "valid"})
    r = en.enrich_email(lead(), c, hunter=h, mx=MX)
    assert r["email_status"] == "verified_provider" and r["email"] == "jane@acme.com"
    h = FakeHunter(find_result={"email": "jane@acme.com", "score": 90, "status": "accept_all"})
    assert en.enrich_email(lead(), c, hunter=h, mx=MX)["email_status"] == "accept_all_risky"
    # inferred candidate rejected by provider -> not returned
    h = FakeHunter(verify_status="invalid", find_result=None)
    r = en.enrich_email(lead(), crawl(), hunter=h, mx=MX)
    assert r["email"] == ""


def test_suppressed_never_returned():
    r = en.enrich_email(lead("Anna Berg"), crawl(), mx=MX, suppressed={"anna.berg@acme.com"})
    assert r["email"] != "anna.berg@acme.com"


def test_phone_prefers_number_next_to_name():
    def finder(text, region):
        out = []
        for num in ("+49301234567", "+49307654321"):
            raw = {"+49301234567": "+49 30 1234567", "+49307654321": "+49 30 7654321"}[num]
            i = text.find(raw)
            if i >= 0:
                out.append({"start": i, "end": i + len(raw), "e164": num, "region": "DE", "line": "landline"})
        return out
    r = en.enrich_phone(lead(), crawl(extra=["https://acme.com/team"]), finder=finder)
    assert r["phone"] == "+49307654321" and r["phone_kind"] == "direct_listed/landline"
    r = en.enrich_phone(lead("Nobody Here"), crawl(), finder=finder)
    assert r["phone"] == "+49301234567" and r["phone_kind"].startswith("company_main")
    assert en.region_for(lead()) == "DE" and en.region_for(lead(country="", domain="x.co.uk")) == "GB"


def test_quality_filter_and_legacy_rows():
    rows = [{"email": "a@x.com", "email_status": "verified_public"}, {"email": "b@x.com", "email_status": "inferred_high"},
            {"email": "c@x.com", "email_status": "guess"}, {"email": "", "email_status": None},
            {"email": "d@x.com", "flags": "email_not_on_source"}, {"email": "e@x.com", "flags": ""}]
    assert len(en.filter_quality(rows, "verified")) == 2   # a + legacy e
    assert len(en.filter_quality(rows, "likely")) == 3
    assert len(en.filter_quality(rows, "all")) == 6


def test_enrich_order_end_to_end():
    db.DB_PATH = tempfile.mktemp(suffix=".db")
    db.init()
    oid = db.x("INSERT INTO orders(title) VALUES('t')")
    lid = db.x("INSERT INTO leads(order_id,company,website,domain,contact_name,title,email,country,source_url,"
               "status,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (oid, "Acme", "https://acme.com", "acme.com", "Anna Berg", "CTO", "", "Germany",
                "https://acme.com/team", "needs_review", "no_email,title_not_on_source"))
    res = en.enrich_order(oid, crawl_fn=lambda d, extra: crawl(d, extra), mx=MX,
                          phone_finder=lambda t, r: [], workers=2)
    assert res["processed"] == 1 and res["verified_public"] == 1
    row = db.q("SELECT * FROM leads WHERE id=?", (lid,))[0]
    assert row["email"] == "anna.berg@acme.com" and row["status"] == "auto_verified"
    assert "no_email" not in row["flags"] and row["enriched_at"]
    # idempotent unless redo
    assert en.enrich_order(oid, crawl_fn=lambda d, e: crawl(d, e), mx=MX, phone_finder=lambda t, r: [])["processed"] == 0
