#!/bin/bash
# Host nginx hardening for whpanel.myaiwhe.com (run on the panel server as root). Idempotent; backs the old file up once.
set -euo pipefail
CF4="173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22"
CF6="2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32"

[ -f /etc/nginx/sites-enabled/whpanel.bak-pre-hardening ] || cp /etc/nginx/sites-available/whpanel /root/whpanel.nginx.bak-pre-hardening 2>/dev/null || cp /etc/nginx/sites-enabled/whpanel /root/whpanel.nginx.bak-pre-hardening

{
  echo "# Cloudflare's addresses: the real visitor address is taken from CF-Connecting-IP only when the connection really comes from Cloudflare."
  for r in $CF4 $CF6; do echo "set_real_ip_from $r;"; done
  echo "real_ip_header CF-Connecting-IP;"
  echo "real_ip_recursive on;"
  echo
  echo "# 1 for a connection from Cloudflare, 0 for anyone else (used to refuse direct visits to the server's own address)."
  echo 'geo $realip_remote_addr $from_cloudflare {'
  echo "    default 0;"
  echo "    127.0.0.1 0;"
  for r in $CF4 $CF6; do echo "    $r 1;"; done
  echo "}"
} > /etc/nginx/conf.d/cloudflare.conf

# A second log with the time each request took: $request_time (everything, from the first byte in to the last byte out) and
# $upstream_response_time (only the panel's own answer), so the server's share of a slow page can be told from Cloudflare's and the network's.
cat > /etc/nginx/conf.d/whpanel-log.conf <<'EOF'
log_format whp_timed '$remote_addr [$time_local] "$request" $status $body_bytes_sent '
                     'rt=$request_time urt=$upstream_response_time cf=$http_cf_ray "$http_user_agent"';
EOF

cat > /etc/nginx/conf.d/whpanel-limits.conf <<'EOF'
# Per visitor address. 429 when a limit is passed. Sign-in, registration, reset, codes (30 a minute, a burst of 20: many people can share one address); the domain search; the API's sign-in.
limit_req_zone $binary_remote_addr zone=whp_auth:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=whp_search:10m rate=40r/m;
limit_req_zone $binary_remote_addr zone=whp_api_auth:10m rate=12r/m;
limit_req_status 429;
EOF

mkdir -p /etc/nginx/snippets
cat > /etc/nginx/snippets/whpanel-proxy.conf <<'EOF'
proxy_pass http://127.0.0.1:8010;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 120s;
EOF

cat > /etc/nginx/sites-enabled/whpanel <<'EOF'
server {
    server_name whpanel.myaiwhe.com;
    client_max_body_size 25M;

    # Only Cloudflare may reach this address: a visit straight to the server's own IP is dropped.
    if ($from_cloudflare = 0) { return 444; }

    # The usual log, and a second one with the request times (see whpanel-log.conf). Naming one here replaces the inherited one, so both are listed.
    access_log /var/log/nginx/access.log;
    access_log /var/log/nginx/whpanel-timed.log whp_timed;

    # Sign-in, registration, password reset and the emailed sign-in code.
    location ~ ^/account/(login|register|password-reset|mfa)/ {
        limit_req zone=whp_auth burst=20 nodelay;
        include snippets/whpanel-proxy.conf;
    }
    location ^~ /api/v1/auth/ {
        limit_req zone=whp_api_auth burst=8 nodelay;
        include snippets/whpanel-proxy.conf;
    }
    # The domain search asks the registrar.
    location ^~ /domains/ {
        limit_req zone=whp_search burst=25 nodelay;
        include snippets/whpanel-proxy.conf;
    }
    location / {
        include snippets/whpanel-proxy.conf;
    }

    listen [::]:443 ssl; # managed by Certbot
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/whpanel.myaiwhe.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/whpanel.myaiwhe.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}
server {
    if ($host = whpanel.myaiwhe.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot


    listen 80;
    listen [::]:80;
    server_name whpanel.myaiwhe.com;
    return 404; # managed by Certbot
}
EOF

# no version in the Server header and error pages
sed -i 's/^\(\s*\)server_tokens build;.*/\1server_tokens off;/' /etc/nginx/nginx.conf
nginx -t
systemctl reload nginx
echo "nginx reloaded"
