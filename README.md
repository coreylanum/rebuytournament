# Hold'em League automation

Automates the weekly PokerNow tournament: results, payouts, standings, a
website and the recap email. Phase 1 of the 2027 plan.

## What happens each week

1. **Host** plays the tournament on PokerNow as usual (created from the shared group account).
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
3. Fill in `infra/cdk.json` context: `group_email`, `treasurer_email`, `from_email`, `region`.

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

Expected cost: a few dollars a month (Lambda, S3, CloudFront), plus a few cents of Claude usage per recap.

## Not in Phase 1 yet

- Creating the PokerNow tournament and sending the invite (Phase 2)
- Fetching logs automatically: PokerNow's CSV download needs a captcha, so the host uploads them
- Matching Venmo/PayPal payment emails automatically (Phase 3); for now the treasurer ticks who paid
