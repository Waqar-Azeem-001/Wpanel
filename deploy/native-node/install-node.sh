#!/bin/sh
# UNTESTED: never run on a real node. Read it and try it on a disposable AlmaLinux 9 machine first.
# usage: install-node.sh PANEL_URL ENROLMENT_TOKEN CAPABILITIES   (binary expected at ./wh-agent)
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
PANEL="$1"; TOKEN="$2"; CAPS="${3:-web,php,database}"
install -m 0755 ./wh-agent /usr/local/bin/wh-agent
install -d -m 0750 /etc/whp /var/lib/whp /var/lib/whp/stage
groupadd -f whp-sftp
install -d -m 0755 /etc/whp/vhosts
grep -q hidepid /etc/fstab || echo "proc /proc proc defaults,hidepid=2 0 0" >> /etc/fstab
printf 'net.ipv4.tcp_syncookies = 1\nkernel.dmesg_restrict = 1\n' > /etc/sysctl.d/90-whp.conf
sysctl --system >/dev/null
/usr/local/bin/wh-agent enroll --panel "$PANEL" --token "$TOKEN" --capabilities "$CAPS"
install -m 0644 ./wh-agent.service /etc/systemd/system/wh-agent.service
systemctl daemon-reload
systemctl enable --now wh-agent
echo "agent installed. Apache include, sshd chroot block and mail-role configs are NOT done by this script yet."
