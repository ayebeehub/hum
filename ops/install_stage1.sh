#!/usr/bin/env bash
# HUM Stage 1 installer — run ONCE as root on the VPS host.
#
# What it does (nothing else):
#   1. copies the payload into /opt/itguy/hum-payload/ (root:root, the durable
#      source for future re-installs)
#   2. installs the three HUM vetted scripts into /opt/itguy/vetted/
#      (root:root 755 — itguy can execute, never modify)
#   3. whitelists those exact three paths for itguy in /etc/sudoers.d/itguy-observe
#      with automatic rollback if the file fails validation
#   4. runs exec_hum_install.sh, which installs the service, the 90s sampler
#      timer and the ledger directory, then verifies the endpoint end to end
#
# It does NOT change any website, container, DNS record, Tailscale setting or
# any database. Nora's state.db is never opened by any of this.
#
# Rollback: sudo /opt/itguy/vetted/exec_hum_control.sh stop
#           sudo systemctl disable --now hum-sampler.timer
#           rm /etc/systemd/system/hum.service /etc/systemd/system/hum-sampler.{service,timer}
#           sudo systemctl daemon-reload
#           rm -rf /opt/itguy/hum /opt/itguy/hum-payload /var/lib/hum
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must be run as root" >&2
  exit 1
fi

STAGE=/opt/itguy/temp/hum_stage
DEST=/opt/itguy/vetted
SUDOERS_FILE=/etc/sudoers.d/itguy-observe
SCRIPTS="exec_hum_install.sh exec_hum_control.sh obs_hum.sh"

for s in $SCRIPTS; do
  [ -f "$STAGE/$s" ] || { echo "ERROR: missing $STAGE/$s" >&2; exit 1; }
done
for f in usage.py hum_merge.py hum_server.py hum_sampler.sh hum.service hum-sampler.service hum-sampler.timer; do
  [ -f "$STAGE/payload/$f" ] || { echo "ERROR: missing $STAGE/payload/$f" >&2; exit 1; }
done

# 1. durable payload
install -d -o root -g root -m 0755 /opt/itguy/hum-payload
install -o root -g root -m 0644 "$STAGE"/payload/* /opt/itguy/hum-payload/

# 2. vetted scripts
for s in $SCRIPTS; do
  install -o root -g root -m 0755 "$STAGE/$s" "$DEST/$s"
done

# 3. sudoers (explicit full paths, no globs; validated with rollback)
cp -a "$SUDOERS_FILE" "$SUDOERS_FILE.itguybak"
for s in $SCRIPTS; do
  if ! grep -qF "$DEST/$s" "$SUDOERS_FILE"; then
    echo "itguy ALL=(ALL) NOPASSWD: $DEST/$s" >> "$SUDOERS_FILE"
  fi
done
chmod 440 "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
  echo "ERROR: sudoers validation failed — restoring backup" >&2
  cp -a "$SUDOERS_FILE.itguybak" "$SUDOERS_FILE"
  exit 1
fi

echo "=== installed vetted scripts ==="
grep -F 'hum' "$SUDOERS_FILE"

# 4. install and verify the service
echo
echo "=== running exec_hum_install.sh ==="
"$DEST/exec_hum_install.sh"