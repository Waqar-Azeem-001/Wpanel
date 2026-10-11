#!/bin/sh
# WHPanel native node installer.
#
# STATUS: UNTESTED. This script has never been run on a real machine. It was written from the documentation of each program and from what the
# agent expects (docs/n3, n6, n7, n9). Try it on a disposable AlmaLinux 9 machine first, read every step, and fix this file with what you learn.
#
# usage:  install-node.sh PANEL_URL ENROLMENT_TOKEN ROLES [ACME_EMAIL]
#         ROLES is a comma list: web  and/or  mail      (a node can be both, but a separate mail node is recommended: ADR 06)
#         ./wh-agent (the binary built from agent/) and ./wh-agent.service must be in the current folder.
#
# What it does, in order: packages, folders and groups, kernel/mount settings the isolation check looks for, Apache include and the suspended page,
# sshd chroot include, (mail role) Postfix/Dovecot/Rspamd base settings that read the files the agent writes, then enrols the agent and starts it.
set -eu

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ "$#" -ge 3 ] || { echo "usage: $0 PANEL_URL TOKEN ROLES [ACME_EMAIL]" >&2; exit 1; }
PANEL="$1"; TOKEN="$2"; ROLES="$3"; ACME_EMAIL="${4:-}"
has_role() { case ",$ROLES," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
say() { echo "== $*"; }

CAPS="metrics"
has_role web && CAPS="web,php,database,ssl,dns,backup,$CAPS"
has_role mail && CAPS="mail,backup,$CAPS"

# --- packages -------------------------------------------------------------------------------------------------------------------------------
say "packages"
dnf -y install epel-release
dnf -y install https://rpms.remirepo.net/enterprise/remi-release-9.rpm || true
dnf -y install openssh-server xfsprogs rsync zstd tar gzip policycoreutils-python-utils
if has_role web; then
    dnf -y install httpd mod_ssl mariadb-server certbot
    for v in 81 82 83 84; do dnf -y install "php$v-php-fpm" "php$v-php-mysqlnd" "php$v-php-gd" "php$v-php-mbstring" "php$v-php-xml" "php$v-php-intl" "php$v-php-opcache" || true; done
fi
if has_role mail; then
    dnf -y install postfix dovecot rspamd redis
fi
dnf -y install restic || say "restic is not in the repositories: install it from https://restic.net before enabling backups"

# --- folders and groups ---------------------------------------------------------------------------------------------------------------------
say "folders and groups"
groupadd -f whp-sftp
install -d -m 0750 /etc/whp /etc/whp/php /var/lib/whp /var/lib/whp/accounts /var/lib/whp/mail /var/lib/whp/stage /var/lib/whp/stage/migrations /var/backups/whp
install -d -m 0755 /srv/whp/sftp /etc/httpd/conf.d/whp /etc/postfix/whp /etc/dovecot/whp /run/whp-php
if has_role mail; then
    groupadd -f -g 5000 vmail
    id vmail >/dev/null 2>&1 || useradd -u 5000 -g 5000 -d /var/mail/vhosts -s /sbin/nologin vmail
    install -d -m 0770 -o vmail -g vmail /var/mail/vhosts
fi

# --- kernel and mount settings (what ISOLATION_CHECK looks for) ---------------------------------------------------------------------------
say "isolation baseline"
if ! grep -q 'hidepid' /etc/fstab; then
    echo "proc /proc proc defaults,hidepid=2 0 0" >> /etc/fstab
    mount -o remount,hidepid=2 /proc || say "could not remount /proc now: it applies at the next boot"
fi
cat > /etc/sysctl.d/90-whp.conf <<'EOF'
fs.protected_symlinks = 1
fs.protected_hardlinks = 1
kernel.yama.ptrace_scope = 1
kernel.dmesg_restrict = 1
net.ipv4.tcp_syncookies = 1
EOF
sysctl --system >/dev/null
if has_role web; then
    # per-account disk and file limits are XFS project quotas on /home: the mount must carry prjquota. This script does NOT reformat or remount it.
    if ! findmnt -no OPTIONS /home 2>/dev/null | grep -q prjquota; then
        say "WARNING: /home is not a separate XFS mount with prjquota. Create it (and add prjquota to its fstab options) before hosting accounts; the isolation check will say so."
    fi
fi

# --- web role -------------------------------------------------------------------------------------------------------------------------------
if has_role web; then
    say "apache"
    install -d -m 0755 /var/www/whp-suspended
    cat > /var/www/whp-suspended/index.html <<'EOF'
<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Website suspended</title></head>
<body style="font-family:sans-serif;text-align:center;margin-top:15vh"><h1>This website is temporarily unavailable</h1>
<p>The account is suspended. If you own this website, please contact your hosting provider.</p></body></html>
EOF
    cat > /etc/httpd/conf.d/00-whp.conf <<'EOF'
# managed by the WHPanel node installer
ServerTokens Prod
ServerSignature Off
TraceEnable Off
IncludeOptional /etc/httpd/conf.d/whp/*.conf
EOF
    httpd -t
    systemctl enable --now httpd
    systemctl enable --now mariadb
    # the account's own PHP-FPM masters are started by the agent as systemd units; the stock pool of each Remi package must not run
    for v in 81 82 83 84; do systemctl disable --now "php$v-php-fpm" 2>/dev/null || true; done
fi

# --- sftp / ssh -----------------------------------------------------------------------------------------------------------------------------
say "sshd"
grep -q '^Include /etc/ssh/sshd_config.d/\*.conf' /etc/ssh/sshd_config || sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config
install -d -m 0755 /etc/ssh/sshd_config.d
# the agent writes 60-whp-sftp.conf itself when the first SFTP login is made; this only makes sure the file the include points at exists and the daemon is sound
sshd -t
systemctl enable --now sshd

# --- mail role ------------------------------------------------------------------------------------------------------------------------------
if has_role mail; then
    say "postfix, dovecot, rspamd (base settings that read the agent's files)"
    # Postfix reads the maps the agent writes under /etc/postfix/whp/ (it runs postmap and reloads).
    for f in virtual_domains virtual_mailboxes virtual_aliases blocked_senders; do [ -f "/etc/postfix/whp/$f" ] || : > "/etc/postfix/whp/$f"; done
    postmap /etc/postfix/whp/virtual_domains /etc/postfix/whp/virtual_mailboxes /etc/postfix/whp/virtual_aliases /etc/postfix/whp/blocked_senders
    postconf -e \
        "virtual_mailbox_domains = hash:/etc/postfix/whp/virtual_domains" \
        "virtual_mailbox_maps = hash:/etc/postfix/whp/virtual_mailboxes" \
        "virtual_alias_maps = hash:/etc/postfix/whp/virtual_aliases" \
        "virtual_mailbox_base = /var/mail/vhosts" \
        "virtual_uid_maps = static:5000" "virtual_gid_maps = static:5000" \
        "smtpd_sender_restrictions = check_sender_access hash:/etc/postfix/whp/blocked_senders" \
        "smtpd_milters = inet:127.0.0.1:11332" "non_smtpd_milters = inet:127.0.0.1:11332" "milter_default_action = accept" \
        "virtual_transport = lmtp:unix:private/dovecot-lmtp" \
        "smtpd_tls_security_level = may" "smtp_tls_security_level = may" \
        "disable_vrfy_command = yes"
    cat > /etc/dovecot/conf.d/99-whp.conf <<'EOF'
# managed by the WHPanel node installer
protocols = imap lmtp
mail_location = maildir:/var/mail/vhosts/%d/%n
mail_uid = 5000
mail_gid = 5000
first_valid_uid = 5000
passdb {
  driver = passwd-file
  args = scheme=SHA512-CRYPT username_format=%u /etc/dovecot/whp/passwd
}
userdb {
  driver = passwd-file
  args = username_format=%u /etc/dovecot/whp/passwd
}
service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}
EOF
    [ -f /etc/dovecot/whp/passwd ] || : > /etc/dovecot/whp/passwd
    chmod 0640 /etc/dovecot/whp/passwd; chgrp dovecot /etc/dovecot/whp/passwd
    install -d -m 0755 /etc/rspamd/local.d /etc/rspamd/dkim
    cat > /etc/rspamd/local.d/worker-proxy.inc <<'EOF'
milter = yes;
timeout = 120s;
upstream "local" { default = yes; self_scan = yes; }
bind_socket = "127.0.0.1:11332";
EOF
    postfix check
    systemctl enable --now redis rspamd postfix dovecot
    say "NOTE: a mail node still needs a TLS certificate for its own hostname, a PTR record and the firewall opened for 25/465/587/993 (not done here)."
fi

# --- firewall -------------------------------------------------------------------------------------------------------------------------------
if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    say "firewall"
    firewall-cmd --permanent --add-service=ssh
    has_role web && { firewall-cmd --permanent --add-service=http; firewall-cmd --permanent --add-service=https; }
    has_role mail && for s in smtp smtps smtp-submission imaps; do firewall-cmd --permanent --add-service="$s"; done
    firewall-cmd --reload
fi

# --- the agent ------------------------------------------------------------------------------------------------------------------------------
say "agent"
install -m 0755 ./wh-agent /usr/local/bin/wh-agent
if [ -n "$ACME_EMAIL" ]; then
    /usr/local/bin/wh-agent enroll --panel "$PANEL" --token "$TOKEN" --capabilities "$CAPS" --acme-email "$ACME_EMAIL"
else
    /usr/local/bin/wh-agent enroll --panel "$PANEL" --token "$TOKEN" --capabilities "$CAPS"
fi
install -m 0644 ./wh-agent.service /etc/systemd/system/wh-agent.service
systemctl daemon-reload
systemctl enable --now wh-agent
/usr/local/bin/wh-agent check
say "done. Open Staff > Native nodes, wait for the first heartbeat, then run the Isolation check."
