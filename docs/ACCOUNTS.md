# Accounts (283): setting it up

The code is `racecast/accounts.py`; the design and the reasons are in
its header and in issue 283. This is the server side: what to run once,
and which switches turn which feature on. Everything is off until its
variables are in `/etc/xc-predictor.env`, and the site runs either way.

## 1. The tables, once

```
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
/srv/venv/bin/python racecast/accounts.py --init
/srv/venv/bin/python racecast/accounts.py --check     # what the env enables
sudo systemctl restart xc-predictor
```

`--init` is `CREATE TABLE IF NOT EXISTS` under a five-second lock
timeout: safe to rerun, never a pipeline step. These tables hold what
people typed and are never rebuilt or swapped.

Until the tables exist, /login says accounts are not set up; nothing
else on the site changes.

## 2. Mail (magic links)

Without a provider the link is printed to the gunicorn log
(`journalctl -u xc-predictor`) and the page tells the person mail is not
set up. Good enough for you to try it; not for anyone else.

Pick Resend or Postmark (both are one API key and a few DNS records):

```
XCP_MAIL_PROVIDER=resend            # or postmark
XCP_MAIL_KEY=re_...
XCP_MAIL_FROM=Racecast <login@racecast.co>
```

In the provider's dashboard add the sending domain and copy its DKIM
and SPF records into Cloudflare DNS (grey cloud, DNS only). Add a
DMARC record too (`_dmarc` TXT, `v=DMARC1; p=quarantine`). Without
these the links land in spam.

## 3. Google sign-in

1. Google Cloud console: a project, then APIs & Services > OAuth consent
   screen. External. App name Racecast, support email, the homepage
   `https://racecast.co`, a privacy policy URL (the About page will do
   until there is one), authorised domain `racecast.co`. Scopes: only
   `openid`, `email`, `profile`. Publish it (in Testing mode only listed
   test users can sign in).
2. Credentials > Create > OAuth client ID > Web application.
   Authorised redirect URI: `https://racecast.co/auth/google/callback`.
3. Env:

```
XCP_GOOGLE_CLIENT_ID=....apps.googleusercontent.com
XCP_GOOGLE_CLIENT_SECRET=...
```

The "Continue with Google" button appears on /login when both are set.
The code exchanges the code for tokens and reads the userinfo endpoint;
no library. An account is matched by Google subject first, then by
email, so a person who used a magic link before and Google after is one
account. A Google address that Google has not verified is refused.

## 4. Turnstile (stops someone mail-bombing a stranger)

Cloudflare dashboard > Turnstile > Add widget, hostname racecast.co,
managed mode. Env:

```
XCP_TURNSTILE_SITEKEY=0x...
XCP_TURNSTILE_SECRET=0x...
```

The widget renders on /login and the server verifies the response. Off
by default; the per-email (5 an hour) and per-IP (30 an hour) limits on
link requests apply either way.

## 5. Admins

```
XCP_ADMIN_EMAILS=you@wherever.com
```

`/api/me` reports `admin: true` for these; nothing else uses it yet.
The verification queue (a coach confirming an athlete, a school-domain
match, your own review) is the next cut and will hang off this.

## 6. What the site does with a session

- The pages stay anonymous and edge-cacheable. The topbar asks `/api/me`
  (never cached) and swaps "Sign in" for the person's link; an athlete
  page that is theirs gets "Your page"; the recruiting page places a
  signed-in athlete's linked page when the URL names nobody.
- `/login`, `/logout`, `/auth/`, `/account` and `/api/` are no-store.
- Sessions are rows in `account_session` (90 days, renewed on a visit),
  the cookie is `xcp_session` (HttpOnly, Secure over TLS, SameSite=Lax).
  "Sign out everywhere" deletes the account's rows.
- Every state change is a POST carrying the session's CSRF token and an
  Origin or Referer on this host.
- `auth_event` is the audit trail: links sent, logins, claims, removals,
  role changes, deletions, with the client IP from Cloudflare's header.
- Deleting an account deletes its sessions and claims (cascade). Results
  are the public record and stay.

## 7. Accounts are for people 13 or older

The login form asks once; the attestation is stored on the account
(`age_ok`). Middle-school athletes are in the data but cannot hold an
account under this rule; a parent can hold one.
