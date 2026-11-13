# Rebuy Club automation

Automates the weekly PokerNow tournament: results, payouts, standings, a
website and the recap email. Phase 1 of the 2027 plan.

## What happens each week

0. **PokerNow** creates the week's tournament by itself (a weekly repeating tournament in the group's
   PokerNow Club), lets club members register and starts it on schedule. At 10 am Eastern on game day the
   system emails the group the club link, the Venmo/PayPal link to pay the treasurer, and the bounty target.
1. **Host** plays the tournament on PokerNow as usual.
2. After the game, the host downloads each table's log (Log / Ledger → *Download full log*)
   and uploads the CSVs on the admin page. The system works out:
   - the finishing order, bubble and first out, from the knockouts in the logs
   - who knocked out last week's winner (the bounty), and whether it rolls over
   - payouts (55 / 30 / 15, 4th gets their $10 back with 11+ players, bounty funded $5 each by 1st and 2nd)
   - hand highlights: all-in equities, suckouts, biggest pots, and play-style stats
   - a recap email written by Claude (Anthropic API) from those facts
3. **Treasurer** gets a "ready to review" email, maps any new PokerNow names to players,
   ticks who has paid, taps the Venmo links to pay winners, checks the recap and clicks
   **Approve**. That publishes the website and emails the recap to the group.

Nothing reaches the group or the website until the treasurer approves.

## Layout

| Path | What |
|---|---|
| `src/pokerleague/logparser.py`, `analysis.py`, `cards.py` | PokerNow log parsing, finish order, knockouts, equity |
| `src/pokerleague/rules.py` | payouts, bounty rollover, points |
| `src/pokerleague/league.py` | the weekly workflow (draft → approved) |
| `src/pokerleague/standings.py` | season standings, points race, knockout matrix |
| `src/pokerleague/recap.py` | recap facts + Claude via the Anthropic API, or Bedrock (template fallback) |
| `src/pokerleague/handlers/admin.py` | admin web app (Lambda function URL) |
| `src/pokerleague/site.py`, `web/` | public website (static, built from approved results) |
| `src/pokerleague/importers/sheet2026.py` | one-time import of the 2026 Google Sheet |
| `infra/app.py` | AWS CDK stack |

Player contact and payment details live only in the private data bucket
(`private/players.json`); the website gets first-name-plus-initial aliases.

## Run it locally

```bash
uv sync
```

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/python scripts/import_sheet.py data/private/2026-sheet.xlsx
```

```bash
ADMIN_PASSWORD=dev .venv/bin/python scripts/admin_local.py
```

Then open http://localhost:8766/admin. Emails go to `data/store/outbox/`, and approving writes
the site to `build/site/` (serve it with `python3 -m http.server 8765 -d build/site`).
To have recaps written by Claude instead of the template, put your Anthropic API key (only the key) in
`data/private/anthropic_api_key` (gitignored) and start the admin with
`ANTHROPIC_API_KEY_FILE=data/private/anthropic_api_key`. To preview one week's recap:
`.venv/bin/python scripts/recap_preview.py 2026-11-03`.

## Deploy to AWS

One-time setup:

1. Have an Anthropic API key ready (console.anthropic.com). Recaps use Claude Opus 5.5; if the key is
   missing or rejected, the recap falls back to a plain template. (To use Amazon Bedrock instead, set
   `recap_provider` to `bedrock` in `infra/cdk.json` and enable Claude model access in the Bedrock console.)
2. Pick the sender address for emails. It must be a member of the Google Group (or the group must allow
   external posting). SES starts in sandbox mode: request production access, or verify the group address too.
3. Fill in `infra/cdk.json` context: `group_email`, `treasurer_email`, `from_email`, `region`, and
   optionally `site_domain` (see *Custom domain* below).

Deploy:

```bash
./scripts/package_lambda.sh
```

```bash
cd infra && npx aws-cdk@2 bootstrap && npx aws-cdk@2 deploy
```

The outputs give the website URL, the admin URL, and the name of the secret holding the admin password:

```bash
aws secretsmanager get-secret-value --secret-id <AdminPasswordSecret> --query SecretString --output text
```

Store your Anthropic API key. First save it to the gitignored local file (works in zsh and bash; the key
isn't echoed or put on the command line):

```bash
printf 'Anthropic API key: '; stty -echo; IFS= read -r k; stty echo; echo; (umask 077; printf '%s' "$k" > data/private/anthropic_api_key); unset k
```

Then copy it into the secret the stack created (it starts as a placeholder):

```bash
aws secretsmanager put-secret-value --secret-id "$(aws cloudformation describe-stacks --stack-name PokerLeague --query "Stacks[0].Outputs[?OutputKey=='AnthropicApiKeySecret'].OutputValue" --output text)" --secret-string file://data/private/anthropic_api_key
```

Seed the 2026 history and publish the site (bucket names from the stack outputs / console):

```bash
.venv/bin/python scripts/import_sheet.py data/private/2026-sheet.xlsx --bucket <DataBucketName> --site-bucket <site bucket> --distribution <distribution id> --publish
```

### Custom domain

Set `site_domain` in `infra/cdk.json` (a subdomain, e.g. `pokertournament.visualization.partners`). The website
is served at `https://<site_domain>/` and the admin at `https://<site_domain>/admin`; emails link there too.
The old `lambda-url` admin address keeps working.

The first deploy with a domain creates a free AWS certificate and **waits** until you prove you own the
domain. Add these records where the domain's DNS is hosted (for `visualization.partners` that's Route 53,
hosted zone `Z07954442P236X23QGALT`, with Namecheap pointing at the AWS nameservers):

1. **Certificate validation** CNAME: shown in the ACM console (us-east-1) under the certificate. A few minutes
   after it exists the deploy carries on. Leave it in place: AWS uses it to renew the certificate.
2. **The site**: in Route 53, A and AAAA *alias* records to the `CloudFrontDomain` stack output; at a DNS
   provider without aliases, a CNAME to it.

These records are made by hand, not by the stack.

### After changing code

`cdk deploy` uploads whatever is in `build/lambda`, so run both commands after any code change:

```bash
./scripts/package_lambda.sh && (cd infra && npx aws-cdk@2 deploy)
```

Changes to the website files (`src/pokerleague/web/`) appear on the next **Approve**, which rebuilds the site.
Changes made in the admin (approvals, players, recaps) need no deploy.

Expected cost: a few dollars a month (Lambda, S3, CloudFront), plus a few cents of Claude usage per recap.

## Weekly tournament and invite (Phase 2)

PokerNow's terms forbid bots driving the site, so nothing here logs in to PokerNow. Instead PokerNow's own
Club tournaments do the work. One-time setup on the shared group account:

1. Get PokerNow PLUS (clubs are created by PLUS members; joining one is free) and create the club.
2. Club **Settings → Enable Tournaments**.
3. **+ Tournament → Copy from a tournament URL** with a past tournament's link, so the blinds and stack carry over.
   Then set: type **Scheduled**, game day at 7:30 pm, **Repeats: Weekly** on that day, buy-in **0** (money is
   handled outside PokerNow), late registration and re-entry as the group plays today, and
   **Promote on the Club Page** so the next game shows at the top of the club page.
4. Send everyone the club link once so they can join.
5. In the admin, **Settings**: paste the club link, check the start time, fill in the treasurer's Venmo
   (and PayPal.me if used), press **Send a test to the treasurer**, then tick **Send the weekly invite**.

The schedule runs every morning at 10 am Eastern and sends only on the game day set in Settings, once per
week. Add holidays to the skip list. **Send to the group now** sends this week's invite immediately.

Check after the first club game that each table's log can still be downloaded (Log / Ledger → Download full
log); the club archive also keeps hand histories.

## Not automated yet

- Fetching logs automatically: PokerNow's CSV download needs a captcha and its terms forbid bots, so the host
  uploads them. Automating this would need PokerNow's written permission.
- Matching Venmo/PayPal payment emails automatically (Phase 3); for now the treasurer ticks who paid
