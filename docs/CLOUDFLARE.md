# Cloudflare in front of racecast.co

The site is nginx in front of gunicorn on one box, with a Let's Encrypt
certificate from certbot. Cloudflare sits in front of that for DNS,
caching, DDoS cover and visit counts. This is the whole setup, dashboard
first, then the one server step.

## 1. DNS (dashboard: DNS > Records)

| Type  | Name          | Content            | Proxy        |
|-------|---------------|--------------------|--------------|
| A     | racecast.co   | the server's IPv4  | Proxied (orange) |
| CNAME | www           | racecast.co        | Proxied (orange) |

Delete any other A/AAAA records for the bare name or www. Leave MX or
TXT records alone. Grey cloud is DNS only: none of the rest applies to a
grey record.

## 2. SSL/TLS

- Overview: encryption mode **Full (strict)**. The origin has a real
  certificate, so Cloudflare verifies it. Never "Flexible": that sends
  plain HTTP to the box, and certbot renewals break.
- Edge Certificates: **Always Use HTTPS** on. **Automatic HTTPS Rewrites**
  on. Minimum TLS version 1.2.
- After enabling, confirm the certificate still renews through the
  proxy: `certbot renew --dry-run` on the server. Let's Encrypt follows
  the edge's redirect to HTTPS, so this passes; if it ever does not,
  switch certbot to the DNS challenge.

## 3. One site, not two (Rules > Redirect Rules)

One rule: when the hostname equals `www.racecast.co`, redirect 301 to
`https://racecast.co` with the path preserved (the "Redirect from WWW to
Root" template does exactly this). Google then sees one copy of each page.

## 4. Caching (Caching > Configuration)

Leave the defaults. Cloudflare caches static files (CSS, JS, images,
fonts) and not HTML, which is what the site needs: the boards change
after every pipeline run and `/api/rankings` must never be served stale.
Do **not** add a "Cache Everything" rule. After a deploy the static
files carry a new version in their URL (`static_v`), so no purge is
needed.

Browser Cache TTL: "Respect Existing Headers" (nginx sends 7 days for
/static/).

## 5. Visit counts (Analytics & Logs > Web Analytics)

Free, no script on the page, no cookies, no banner: once the records are
proxied the dashboard shows visits, page views, top pages, countries and
referrers. Enable it for the zone. For the exact pages people open, the
nginx access log on the server is the ground truth, once step 6 makes
it record the visitor rather than the edge; `goaccess` turns it into a
report in one command.

## 6. The server step: log the visitor, not Cloudflare

Proxied, every request arrives from a Cloudflare address. Run once, then
monthly (the ranges change rarely):

    sudo bash /srv/xc-predictor/deploy/cloudflare_realip.sh

It writes `/etc/nginx/conf.d/cloudflare-realip.conf` (trust the
`CF-Connecting-IP` header from Cloudflare's published ranges only) and
reloads nginx. Flask keeps reading `X-Real-IP`, which nginx now sets to
the visitor.

## Order

1 and 2 together, then 3, then 6 on the server, then 5. Nothing here
needs the pipeline stopped or the site restarted.
