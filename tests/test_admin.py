import base64
import os
from pathlib import Path

import pytest

os.environ["ADMIN_PASSWORD"] = "test-password"
os.environ["SIGNING_KEY"] = "test-key"
for var in ("DATA_BUCKET", "SITE_BUCKET", "FROM_EMAIL", "BEDROCK_REGION", "AWS_REGION", "ADMIN_PASSWORD_SECRET_ARN",
            "SIGNING_KEY_SECRET_ARN", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_FILE", "ANTHROPIC_API_KEY_SECRET_ARN", "RECAP_PROVIDER"):
    os.environ.pop(var, None)  # tests never call real services

from pokerleague.handlers import admin  # noqa: E402
from pokerleague.league import get_tournament, save_registry, save_tournament  # noqa: E402
from pokerleague.outbound import venmo_pay_link, venmo_username  # noqa: E402
from pokerleague.store import LocalStore  # noqa: E402

from test_core import LOGS, registry_1103  # noqa: E402


def event(method, path, body=b"", headers=None, cookie=None, query=""):
    h = dict(headers or {})
    if cookie:
        h["cookie"] = cookie
    return {"rawPath": path, "rawQueryString": query, "headers": h, "body": base64.b64encode(body).decode(),
            "isBase64Encoded": True, "requestContext": {"http": {"method": method}}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SITE_DIR", str(tmp_path / "site"))
    monkeypatch.setenv("GROUP_EMAIL", "group@example.com")
    monkeypatch.setenv("TREASURER_EMAIL", "treasurer@example.com")
    admin.settings.cache_clear()
    store = LocalStore(tmp_path / "store")
    save_registry(store, registry_1103())
    save_tournament(store, {"date": "2026-10-27", "season": 2026, "status": "approved", "source": "sheet",
                            "results": [{"player": "suzanne-b", "place": 1}], "placings": {"first": "suzanne-b"},
                            "bounty": {"winners": [], "carry_out": 0.0}})
    yield store, tmp_path
    admin.settings.cache_clear()


def login(store):
    r = admin.route(event("POST", "/admin/login", b"password=test-password",
                          {"content-type": "application/x-www-form-urlencoded"}), store)
    assert r["statusCode"] == 303
    return r["cookies"][0].split(";")[0]


def multipart(fields, files):
    boundary = "----testboundary"
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for name, filename, data in files:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: text/csv\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, {"content-type": f"multipart/form-data; boundary={boundary}"}


def test_requires_login(env):
    store, _ = env
    assert admin.route(event("GET", "/admin"), store)["headers"]["location"] == "/admin/login"
    bad = admin.route(event("GET", "/admin", cookie="session=9999999999.forged"), store)
    assert bad["statusCode"] == 303


def test_wrong_password_rejected(env, monkeypatch):
    store, _ = env
    monkeypatch.setattr(admin.time, "sleep", lambda s: None)
    r = admin.route(event("POST", "/admin/login", b"password=nope", {"content-type": "application/x-www-form-urlencoded"}), store)
    assert r["statusCode"] == 200 and "didn" in r["body"]


def test_upload_review_approve(env):
    store, tmp = env
    cookie = login(store)
    body, headers = multipart({"date": "2026-11-03", "host": "Corey L"},
                              [("logs", p.name, p.read_bytes()) for p in LOGS])
    r = admin.route(event("POST", "/admin/upload", body, headers, cookie), store)
    assert r["statusCode"] == 303 and r["headers"]["location"].startswith("/admin/t/2026-11-03")

    record = get_tournament(store, "2026-11-03")
    assert record["placings"]["first"] == "doug-b"
    assert record["recap"]["source"] == "template"  # no Bedrock region in tests
    assert any("ready to review" in store.get_bytes(k).decode() for k in store.list("outbox/"))

    page = admin.route(event("GET", "/admin/t/2026-11-03", cookie=cookie), store)
    assert page["statusCode"] == 200 and "Pay the winners" in page["body"]

    paid = "&".join(f"paid={p}" for p in ["doug-b", "dave-b", "john-p", "susie-b", "david-a", "rob-b",
                                          "suzanne-b", "jim-o", "charlie-h", "heather-b"])
    admin.route(event("POST", "/admin/t/2026-11-03/paid", paid.encode(), {"content-type": "application/x-www-form-urlencoded"}, cookie), store)
    transfers = {t["player"]: t["amount"] for t in get_tournament(store, "2026-11-03")["payouts"]["transfers"]}
    assert transfers == {"doug-b": 51.0, "corey-l": 31.0, "paul-w": 8.0, "dave-b": 10.0}

    r = admin.route(event("POST", "/admin/t/2026-11-03/approve", b"send_email=yes", {"content-type": "application/x-www-form-urlencoded"}, cookie), store)
    assert r["statusCode"] == 303
    assert get_tournament(store, "2026-11-03")["status"] == "approved"
    assert (tmp / "site" / "data" / "t" / "2026-11-03.json").exists()
    emails = [store.get_bytes(k).decode() for k in store.list("outbox/")]
    assert any("Doug B wins" in m and "group@example.com" in m for m in emails)
    # Private data never reaches the published site.
    site_text = "".join(p.read_text() for p in (tmp / "site" / "data").rglob("*.json"))
    assert "paid" not in site_text.replace("paid_out", "") and "@" not in site_text


def test_html_is_escaped(env):
    store, _ = env
    cookie = login(store)
    from pokerleague.league import load_registry
    reg = load_registry(store)
    reg.get("doug-b").venmo = '<script>alert(1)</script>'
    save_registry(store, reg)
    page = admin.route(event("GET", "/admin/players", cookie=cookie), store)
    assert "<script>alert" not in page["body"] and "&lt;script&gt;" in page["body"]


def test_venmo_helpers():
    assert venmo_username("@Aaron-Pride-1") == "Aaron-Pride-1"
    assert venmo_username("Donna Ball @hdball") == "hdball"
    assert venmo_username("Corey-Lanum") == "Corey-Lanum"
    assert venmo_username("Aidan Anthony") is None
    assert venmo_pay_link("Corey-Lanum", 31, "Poker 2026-11-03") == \
        "https://venmo.com/Corey-Lanum?txn=pay&amount=31.00&note=Poker+2026-11-03"


def test_public_base_url_prefers_custom_domain(env, monkeypatch):
    req = admin.Request(event("GET", "/admin", headers={"host": "abc.lambda-url.us-east-1.on.aws"}))
    assert admin.public_base_url(req) == "https://abc.lambda-url.us-east-1.on.aws/"
    monkeypatch.setenv("ADMIN_URL", "https://poker.example.com")
    admin.settings.cache_clear()
    assert admin.public_base_url(req) == "https://poker.example.com/"
    local = admin.Request(event("GET", "/admin", headers={"host": "localhost:8766"}))
    monkeypatch.delenv("ADMIN_URL")
    admin.settings.cache_clear()
    assert admin.public_base_url(local) == "http://localhost:8766/"
