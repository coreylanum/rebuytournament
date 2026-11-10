"""Admin web app (Lambda function URL): upload logs, review, approve.

Who uses it:
  host       uploads the PokerNow table logs after the game
  treasurer  maps unknown players, ticks who paid, checks payouts and the
             recap, then approves. Approval publishes the website and emails
             the recap to the group.

One shared admin password; a signed cookie keeps people logged in.
Every value rendered into HTML goes through html.escape.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import time
import traceback
from datetime import date as Date, timedelta
from email.parser import BytesParser
from email.policy import default as default_policy
from urllib.parse import parse_qs, quote

from .. import league
from ..config import settings
from ..outbound import paypal_link, publish_site, send_email, venmo_pay_link, venmo_username
from ..players import Player, slugify
from ..recap import build_facts, generate_recap
from ..store import Store, store_from_env

log = logging.getLogger(__name__)
SESSION_DAYS = 30
e = html.escape


# ---------- request / response plumbing ----------

class Request:
    def __init__(self, event: dict):
        http = event.get("requestContext", {}).get("http", {})
        self.method = http.get("method", "GET").upper()
        self.path = event.get("rawPath", "/").rstrip("/") or "/"
        self.headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        self.query = {k: v[-1] for k, v in parse_qs(event.get("rawQueryString", "")).items()}
        body = event.get("body") or ""
        self.body = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
        cookies = event.get("cookies") or [c.strip() for c in self.headers.get("cookie", "").split(";") if c.strip()]
        self.cookies = dict(c.split("=", 1) for c in cookies if "=" in c)
        self._form = None
        self._files = None

    def _parse(self) -> None:
        self._form, self._files = {}, []
        ctype = self.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            msg = BytesParser(policy=default_policy).parsebytes(f"Content-Type: {ctype}\r\n\r\n".encode() + self.body)
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename:
                    self._files.append((name, filename, payload))
                elif name:
                    self._form.setdefault(name, []).append(payload.decode("utf-8", "replace"))
        else:
            for k, v in parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True).items():
                self._form[k] = v

    def form(self, name: str, default: str = "") -> str:
        if self._form is None:
            self._parse()
        return (self._form.get(name) or [default])[-1].strip()

    def form_list(self, name: str) -> list[str]:
        if self._form is None:
            self._parse()
        return self._form.get(name, [])

    def files(self) -> list[tuple[str, str, bytes]]:
        if self._files is None:
            self._parse()
        return self._files


def response(body: str, status: int = 200, cookies: list[str] | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store",
            "x-frame-options": "DENY",
            "content-security-policy": "default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'",
        },
        "cookies": cookies or [],
        "body": body,
    }


def redirect(location: str, cookies: list[str] | None = None) -> dict:
    return {"statusCode": 303, "headers": {"location": location, "cache-control": "no-store"}, "cookies": cookies or [], "body": ""}


def flash_redirect(location: str, message: str) -> dict:
    sep = "&" if "?" in location else "?"
    return redirect(f"{location}{sep}msg={quote(message)}")


# ---------- auth ----------

def _sign(value: str) -> str:
    return hmac.new(settings().signing_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def session_cookie() -> str:
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"session={expires}.{_sign(expires)}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={SESSION_DAYS * 86400}"


def logged_in(req: Request) -> bool:
    token = req.cookies.get("session", "")
    expires, _, sig = token.partition(".")
    if not expires.isdigit() or int(expires) < time.time() or not settings().signing_key:
        return False
    return hmac.compare_digest(sig, _sign(expires))


# ---------- page shell ----------

STYLE = """
:root{color-scheme:light dark;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--border:rgba(11,11,11,.1);--accent:#2a78d6;--warn:#8a5a00;--warnbg:#fff4d6;--good:#006300}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--border:rgba(255,255,255,.1);--accent:#3987e5;--warn:#fab219;--warnbg:#2b2410;--good:#0ca30c}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;gap:16px;align-items:center;max-width:960px;margin:0 auto;padding:16px;flex-wrap:wrap}header a{color:var(--ink2);text-decoration:none}header strong{flex:1}
main{max-width:960px;margin:0 auto;padding:0 16px 64px}h1{font-size:24px;margin:8px 0 16px}h2{font-size:17px;margin:0 0 12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}th{color:var(--ink2);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
input[type=text],input[type=password],input[type=date],input[type=url],select,textarea{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px;max-width:100%}
textarea{width:100%;min-height:260px;line-height:1.5}
button{font:inherit;border-radius:8px;padding:7px 14px;border:1px solid var(--border);background:var(--surface);color:var(--ink);cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:end}.row label{display:flex;flex-direction:column;gap:4px;color:var(--ink2);font-size:13px}
.note{background:var(--warnbg);color:var(--ink);border-radius:10px;padding:10px 14px;margin-bottom:16px}.ok{color:var(--good)}.muted{color:var(--muted)}.recap{white-space:pre-wrap;max-width:70ch}
a{color:var(--accent)}form.inline{display:inline}
"""


def page(title: str, body: str, req: Request | None = None) -> dict:
    cfg = settings()
    msg = req.query.get("msg") if req else None
    nav = ""
    if req and logged_in(req):
        nav = (f'<a href="/admin">Tournaments</a><a href="/admin/players">Players</a>'
               f'<a href="{e(cfg.site_url)}">Website</a>'
               '<form class="inline" method="post" action="/admin/logout"><button>Log out</button></form>')
    return response(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · {e(cfg.league_name)} admin</title><style>{STYLE}</style></head>
<body><header><strong>{e(cfg.league_name)} admin</strong>{nav}</header><main>
{f'<div class="note">{e(msg)}</div>' if msg else ''}{body}</main></body></html>""")


def last_tuesday() -> str:
    today = Date.today()
    return (today - timedelta(days=(today.weekday() - 1) % 7)).isoformat()


# ---------- views ----------

def view_login(req: Request, error: str = "") -> dict:
    return page("Log in", f"""<h1>Log in</h1><div class="card"><form method="post" action="/admin/login" class="row">
<label>Admin password<input type="password" name="password" autofocus required></label><button class="primary">Log in</button></form>
{f'<p class="muted">{e(error)}</p>' if error else ''}</div>""", req)


def view_dashboard(store: Store, req: Request) -> dict:
    tournaments = list(reversed(league.all_tournaments(store)))
    registry = league.load_registry(store)
    hosts = sorted({p.alias for p in registry.players.values()})
    rows = []
    for t in tournaments[:30]:
        first = t.get("placings", {}).get("first")
        status = t.get("status", "draft")
        flag = " · needs player mapping" if t.get("unmatched") else ""
        rows.append(f'<tr><td><a href="/admin/t/{e(t["date"])}">{e(t["date"])}</a></td><td>{e(status)}{e(flag)}</td>'
                    f'<td>{e(registry.public().get(first, first or ""))}</td><td class="num">{t.get("players") or len(t.get("results", []))}</td>'
                    f'<td>{e(t.get("source", ""))}</td></tr>')
    host_opts = "".join(f"<option>{e(h)}</option>" for h in hosts)
    return page("Tournaments", f"""<h1>Tournaments</h1>
<div class="card"><h2>Upload this week's logs</h2>
<p class="muted">In PokerNow, open each table of the tournament, then Log / Ledger → Download full log. Upload every table's CSV (you can add more tables later).</p>
<form method="post" action="/admin/upload" enctype="multipart/form-data" class="row">
<label>Tournament date<input type="date" name="date" value="{e(last_tuesday())}" required></label>
<label>Host<input type="text" name="host" list="hosts"><datalist id="hosts">{host_opts}</datalist></label>
<label>PokerNow tournament link<input type="url" name="tournament_url" placeholder="https://www.pokernow.com/mtt/..."></label>
<label>Table logs (CSV)<input type="file" name="logs" accept=".csv,text/csv" multiple required></label>
<button class="primary">Upload</button></form></div>
<div class="card"><h2>All tournaments</h2><table><thead><tr><th>Date</th><th>Status</th><th>Winner</th><th class="num">Players</th><th>Source</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>""", req)


def _player_options(registry, selected: str = "") -> str:
    opts = ['<option value="">Choose a player…</option>']
    for p in sorted(registry.players.values(), key=lambda p: p.alias.lower()):
        sel = " selected" if p.id == selected else ""
        opts.append(f'<option value="{e(p.id)}"{sel}>{e(p.alias)}</option>')
    return "".join(opts)


def view_tournament(store: Store, req: Request, date: str) -> dict:
    t = league.get_tournament(store, date)
    if not t:
        return page("Not found", "<h1>Not found</h1>", req)
    registry = league.load_registry(store)
    name = lambda pid: registry.public().get(pid, pid or "–")  # noqa: E731
    approved = t.get("status") == "approved"
    parts = [f"<h1>{e(date)} <span class='muted'>· {e(t.get('status', 'draft'))}</span></h1>"]
    if t.get("source") == "sheet":
        parts.append('<div class="card">Imported from the 2026 spreadsheet; nothing to review.</div>')
        return page(date, "".join(parts), req)

    for w in t.get("warnings", []):
        parts.append(f'<div class="note">Log check: {e(w)}</div>')

    # 1. Unknown players
    if t.get("unmatched"):
        rows = []
        for u in t["unmatched"]:
            rows.append(f"""<tr><td>{e(u['handle'])}<div class="muted">ID {e(u['pokernow_id'])}</div></td><td>
<form method="post" action="/admin/t/{e(date)}/assign" class="row"><input type="hidden" name="pokernow_id" value="{e(u['pokernow_id'])}">
<label>Existing player<select name="player_id">{_player_options(registry)}</select></label>
<label>or new player (first name + last initial)<input type="text" name="new_alias" placeholder="Pat Q"></label>
<button>Save</button></form></td></tr>""")
        parts.append(f"""<div class="card"><h2>1. Who are these players?</h2>
<p class="muted">These PokerNow names/IDs aren't on the player list yet. Once mapped, they're recognized automatically next time.</p>
<table><tbody>{''.join(rows)}</tbody></table></div>""")

    # 2. Results + paid checkboxes
    paid = set(t.get("paid", []))
    payouts = t.get("payouts", {})
    gross = payouts.get("gross", {})
    rows = []
    for r in t.get("results", []):
        checked = " checked" if r["player"] in paid else ""
        disabled = " disabled" if approved else ""
        rows.append(f'<tr><td class="num">{r["place"]}</td><td>{e(name(r["player"]))}<div class="muted">{e(r["handle"])}</div></td>'
                    f'<td>{e(", ".join(name(p) for p in r["busted_by"]))}</td>'
                    f'<td class="num">{"$%.2f" % gross[r["player"]] if gross.get(r["player"]) else ""}</td>'
                    f'<td><label><input type="checkbox" name="paid" value="{e(r["player"])}"{checked}{disabled}> paid</label></td></tr>')
    p = t.get("placings", {})
    b = t.get("bounty", {})
    bounty_line = (f"Bounty on {e(name(b.get('target')))} (${b.get('pot', 0):.2f}): "
                   + (f"collected by {e(', '.join(name(x) for x in b['winners']))}" if b.get("winners") else
                      f"not collected, rolls over (${b.get('carry_out', 0):.2f} carried)")) if b.get("target") else "No bounty target this week (first tournament)."
    parts.append(f"""<div class="card"><h2>2. Results and buy-ins</h2>
<p>{e(name(p.get('first')))} won. Bubble: {e(name(p.get('bubble')))}. First out: {e(name(p.get('first_out')))}.<br>{bounty_line}</p>
<form method="post" action="/admin/t/{e(date)}/paid"><table><thead><tr><th class="num">Place</th><th>Player</th><th>Knocked out by</th><th class="num">Winnings</th><th>Buy-in</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
{'' if approved else '<p><button>Save who has paid</button> <span class="muted">Winners who have not paid have $10 taken out of their payout.</span></p>'}</form></div>""")

    # 3. Payouts
    transfer_rows = []
    for tr in payouts.get("transfers", []):
        player = registry.get(tr["player"]) or Player(id=tr["player"], alias=tr["player"])
        note = f"Poker {date}"
        venmo = venmo_username(player.venmo)
        links = []
        if venmo:
            links.append(f'<a href="{e(venmo_pay_link(venmo, tr["amount"], note))}">Venmo @{e(venmo)}</a>')
        elif player.venmo:
            links.append(f"Venmo: {e(player.venmo)} (check username)")
        if player.paypal:
            pl = paypal_link(player.paypal, tr["amount"])
            links.append(f'<a href="{e(pl)}">PayPal</a>' if pl else f"PayPal: {e(player.paypal)}")
        deducted = " <span class='muted'>(after $10 unpaid buy-in)</span>" if tr["unpaid_buy_in_deducted"] else ""
        transfer_rows.append(f'<tr><td>{e(player.alias)}</td><td class="num">${tr["amount"]:.2f}{deducted}</td><td>{" · ".join(links) or "<span class=muted>no payment account on file</span>"}</td></tr>')
    owe = "".join(f"<li>{e(name(o['player']))} owes ${o['amount']:.2f}</li>" for o in payouts.get("still_owe", []))
    lines = "".join(f'<tr><td>{e(name(l["player"]))}</td><td>{e(l["reason"])}</td><td class="num">{l["amount"]:+.2f}</td></tr>' for l in payouts.get("lines", []))
    totals = payouts.get("totals", {})
    parts.append(f"""<div class="card"><h2>3. Pay the winners</h2>
<table><thead><tr><th>Player</th><th class="num">Send</th><th>How</th></tr></thead><tbody>{''.join(transfer_rows)}</tbody></table>
{f'<h2 style="margin-top:16px">Still owed to the treasurer</h2><ul>{owe}</ul>' if owe else '<p class="ok">Everyone has paid.</p>'}
<details><summary class="muted">How the payouts were worked out</summary><table><tbody>{lines}</tbody></table>
<p class="muted">Pool ${totals.get('pool', 0):.2f} · collected ${totals.get('collected', 0):.2f} · paid out ${totals.get('paid_out', 0):.2f} · bounty carry change ${totals.get('bounty_carry_change', 0):+.2f}</p></details></div>""")

    # 4. Recap
    recap = t.get("recap") or {}
    if t.get("unmatched"):
        recap_html = '<p class="muted">The recap is written once every player is mapped.</p>'
    else:
        source = recap.get("source", "")
        why = f" ({e(recap.get('reason', ''))})" if source == "template" and recap.get("reason") else ""
        recap_html = f"""<form method="post" action="/admin/t/{e(date)}/recap">
<textarea name="text"{' readonly' if approved else ''}>{e(recap.get('text', ''))}</textarea>
<p class="muted">Written by {e(source or 'nobody yet')}{why}{' · edited' if recap.get('edited') else ''}.</p>
{'' if approved else '<button name="action" value="save">Save edits</button> <button name="action" value="regenerate">Write a new one</button>'}</form>"""
    parts.append(f'<div class="card"><h2>4. Recap email</h2>{recap_html}</div>')

    # 5. Approve
    if approved:
        parts.append(f"""<div class="card"><h2>Approved</h2><p>Approved {e(t.get('approved_at', ''))}. It's on the website.</p>
<form method="post" action="/admin/t/{e(date)}/reopen"><button>Reopen for changes</button></form></div>""")
    else:
        ready = not t.get("unmatched")
        parts.append(f"""<div class="card"><h2>5. Approve</h2>
<p>Approving publishes the results and standings to the website and emails the recap to the group.</p>
<form method="post" action="/admin/t/{e(date)}/approve"><label><input type="checkbox" name="send_email" value="yes" checked> Email the recap to the group</label><br><br>
<button class="primary"{'' if ready else ' disabled'}>Approve and publish</button></form></div>""")
    return page(date, "".join(parts), req)


def view_players(store: Store, req: Request) -> dict:
    registry = league.load_registry(store)
    rows = "".join(
        f'<tr><td><a href="/admin/players/{e(p.id)}">{e(p.alias)}</a></td><td>{e(p.full_name)}</td><td>{e(", ".join(p.handles))}</td>'
        f'<td>{e(p.venmo)}</td><td>{e(p.paypal)}</td></tr>'
        for p in sorted(registry.players.values(), key=lambda p: p.alias.lower()))
    return page("Players", f"""<h1>Players</h1><div class="card"><table><thead><tr><th>Name</th><th>Full name</th><th>PokerNow names</th><th>Venmo</th><th>PayPal</th></tr></thead>
<tbody>{rows}</tbody></table></div>""", req)


PLAYER_FIELDS = [("full_name", "Full name"), ("email", "Email"), ("venmo", "Venmo username"), ("paypal", "PayPal (email or paypal.me link)"), ("phone_last4", "Last 4 of phone (Venmo check)")]


def view_player(store: Store, req: Request, player_id: str) -> dict:
    p = league.load_registry(store).get(player_id)
    if not p:
        return page("Not found", "<h1>Not found</h1>", req)
    fields = "".join(f'<label>{e(label)}<input type="text" name="{k}" value="{e(getattr(p, k))}"></label>' for k, label in PLAYER_FIELDS)
    return page(p.alias, f"""<h1>{e(p.alias)}</h1><div class="card"><form method="post" action="/admin/players/{e(p.id)}" class="row">{fields}
<label>PokerNow names (comma separated)<input type="text" name="handles" value="{e(", ".join(p.handles))}"></label>
<label>PokerNow IDs (comma separated)<input type="text" name="pokernow_ids" value="{e(", ".join(p.pokernow_ids))}"></label>
<button class="primary">Save</button></form></div>""", req)


# ---------- actions ----------

def write_recap(store: Store, record: dict) -> dict:
    registry = league.load_registry(store)
    facts = build_facts(record, league.all_tournaments(store), registry.public())
    result = generate_recap(facts)
    record["recap"] = {**result, "generated_at": league.now(), "edited": False}
    league.save_tournament(store, record)
    return record


def recap_email(record: dict, aliases: dict[str, str]) -> tuple[str, str]:
    cfg = settings()
    winner = aliases.get(record.get("placings", {}).get("first"), "")
    d = Date.fromisoformat(record["date"])
    subject = f"Poker results {d.strftime('%a %b')} {d.day}: {winner} wins"
    text = record.get("recap", {}).get("text", "")
    text += f"\n\nFull results, hands and standings: {cfg.site_url}#/t/{record['date']}"
    return subject, text


def do_upload(store: Store, req: Request) -> dict:
    date = req.form("date")
    Date.fromisoformat(date)  # validates
    files = [(fn, data.decode("utf-8-sig")) for _, fn, data in req.files() if fn.lower().endswith(".csv")]
    if not files:
        return flash_redirect("/admin", "Choose at least one CSV log file.")
    record = league.ingest_logs(store, date, files, tournament_url=req.form("tournament_url") or None, host=req.form("host") or None)
    if not record.get("unmatched"):
        record = write_recap(store, record)
    cfg = settings()
    host = req.headers.get("host")
    admin_url = f"https://{host}/" if host and not host.startswith(("localhost", "127.0.0.1")) else cfg.admin_url
    send_email(store, cfg.treasurer_email, f"Poker results for {date} are ready to review",
               f"Logs for {date} were uploaded ({len(record.get('log_files', []))} table logs).\n\n"
               f"Review payouts and approve: {admin_url}admin/t/{date}\n")
    return flash_redirect(f"/admin/t/{date}", f"Uploaded {len(files)} log file(s).")


def handle_post(store: Store, req: Request, date: str, action: str) -> dict:
    base = f"/admin/t/{date}"
    if action == "assign":
        record = league.assign_player(store, date, req.form("pokernow_id"), req.form("player_id") or None, req.form("new_alias") or None)
        if not record.get("unmatched") and not record.get("recap", {}).get("edited"):
            write_recap(store, record)
        return flash_redirect(base, "Player saved.")
    if action == "paid":
        league.set_paid(store, date, req.form_list("paid"))
        return flash_redirect(base, "Buy-ins saved.")
    if action == "recap":
        record = league.get_tournament(store, date)
        if req.form("action") == "regenerate":
            write_recap(store, record)
            return flash_redirect(base, "Wrote a new recap.")
        record["recap"] = {**record.get("recap", {}), "text": req.form("text"), "edited": True}
        league.save_tournament(store, record)
        return flash_redirect(base, "Recap saved.")
    if action == "approve":
        record = league.approve(store, date, approved_by="admin")
        published = publish_site(store)
        sent = "not emailed"
        if req.form("send_email") == "yes":
            subject, text = recap_email(record, league.load_registry(store).public())
            sent = send_email(store, settings().group_email, subject, text)
        return flash_redirect(base, f"Approved. Website: {published}. Recap: {sent}.")
    if action == "reopen":
        league.reopen(store, date)
        publish_site(store)
        return flash_redirect(base, "Reopened; the website no longer shows this week until it's approved again.")
    return page("Not found", "<h1>Not found</h1>", req)


def save_player(store: Store, req: Request, player_id: str) -> dict:
    registry = league.load_registry(store)
    p = registry.get(player_id)
    if not p:
        return redirect("/admin/players")
    for k, _ in PLAYER_FIELDS:
        setattr(p, k, req.form(k))
    p.handles = [x.strip() for x in req.form("handles").split(",") if x.strip()]
    p.pokernow_ids = [x.strip() for x in req.form("pokernow_ids").split(",") if x.strip()]
    league.save_registry(store, registry)
    return flash_redirect(f"/admin/players/{player_id}", "Saved.")


# ---------- router ----------

def route(event: dict, store: Store | None = None) -> dict:
    req = Request(event)
    store = store or store_from_env()
    path, method = req.path, req.method
    if path in ("/", "/admin/login") and method == "GET":
        return redirect("/admin") if logged_in(req) else view_login(req)
    if path == "/admin/login" and method == "POST":
        if settings().admin_password and hmac.compare_digest(req.form("password"), settings().admin_password):
            return redirect("/admin", [session_cookie()])
        time.sleep(1)
        return view_login(req, "That password didn't work.")
    if path == "/admin/logout" and method == "POST":
        return redirect("/admin/login", ["session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0"])
    if not logged_in(req):
        return redirect("/admin/login")

    parts = path.strip("/").split("/")
    if path == "/admin":
        return view_dashboard(store, req)
    if path == "/admin/upload" and method == "POST":
        return do_upload(store, req)
    if parts[:2] == ["admin", "t"] and len(parts) >= 3:
        date = parts[2]
        Date.fromisoformat(date)
        if method == "GET" and len(parts) == 3:
            return view_tournament(store, req, date)
        if method == "POST" and len(parts) == 4:
            return handle_post(store, req, date, parts[3])
    if path == "/admin/players":
        return view_players(store, req)
    if parts[:2] == ["admin", "players"] and len(parts) == 3:
        player_id = slugify(parts[2])
        return save_player(store, req, player_id) if method == "POST" else view_player(store, req, player_id)
    return page("Not found", "<h1>Not found</h1>", req)


def handler(event: dict, context=None) -> dict:
    try:
        return route(event)
    except ValueError as exc:
        referer = Request(event).headers.get("referer", "/admin")
        path = "/" + referer.split("://", 1)[-1].split("/", 1)[-1] if "://" in referer else "/admin"
        return flash_redirect(path.split("?")[0], f"Couldn't do that: {exc}")
    except Exception:
        log.error("admin error: %s", traceback.format_exc())
        return response("<h1>Something went wrong</h1><p>The error was logged.</p>", 500)
