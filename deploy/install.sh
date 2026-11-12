#!/bin/bash
# Installs or updates PiS2CR on this Pi. Safe to run again; pairings and settings are kept.
# Usage (from the copied PiS2CR folder): sudo bash deploy/install.sh
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

BACKEND_URL=https://github.com/wozt/pcble2gamepad
BACKEND_COMMIT=97084dbdc6f83d16663b32a0721ec6b713e2bbe6
TOTAL=7
step() { echo "STEP $1/$TOTAL $2"; }

require_root
resolve_user
ref=$home/ref/pcble2gamepad
echo "Installing for user $user, files in $repo"

step 1 "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git meson ninja-build libglib2.0-dev libbluetooth-dev libjson-glib-dev bluez python3 openssl rfkill sudo

step 2 "Checking Bluetooth"
rfkill unblock bluetooth || true
command -v hcitool >/dev/null && command -v hciconfig >/dev/null || die "hcitool/hciconfig are missing; the bluez package should provide them."
hciconfig hci0 up 2>/dev/null || true
hciconfig hci0 >/dev/null 2>&1 || die "No Bluetooth adapter hci0 was found. This needs a Raspberry Pi 4 with its built-in Bluetooth."

step 3 "Fetching the Switch backend"
if [[ ! -d $ref/.git ]]; then
    as_user mkdir -p "$home/ref"
    as_user git clone --quiet "$BACKEND_URL" "$ref"
fi
as_user git -C "$ref" cat-file -e "$BACKEND_COMMIT^{commit}" 2>/dev/null || as_user git -C "$ref" fetch --quiet origin

step 4 "Applying the gyro and battery patch"
patch=$repo/deploy/backend-imu-battery.patch
if as_user git -C "$ref" apply --reverse --check "$patch" 2>/dev/null; then
    echo "Patch already applied."
else
    as_user git -C "$ref" checkout --quiet "$BACKEND_COMMIT"
    as_user git -C "$ref" apply "$patch"
fi

step 5 "Building the backend (a few minutes the first time)"
[[ -d $ref/build-pro ]] || as_user meson setup --quiet "$ref/build-pro" "$ref"
as_user ninja -C "$ref/build-pro" pcble2gamepad-pro-poc

step 6 "Installing services"
install -d /usr/local/lib/pis2cr
install -m 755 "$repo/deploy/pis2cr-switch.sh" /usr/local/lib/pis2cr/pis2cr-switch.sh
for unit in "$repo"/deploy/pis2cr-*.service; do
    sed -e "s|@USER@|$user|g" -e "s|@REPO@|$repo|g" "$unit" > "/etc/systemd/system/$(basename "$unit")"
done
install -m 644 "$repo/deploy/pis2cr-no-btusb.conf" /etc/modprobe.d/pis2cr-no-btusb.conf
sudoers=$(mktemp)
sed "s|@USER@|$user|g" "$repo/deploy/pis2cr-hcitool.sudoers" > "$sudoers"
visudo -cf "$sudoers" >/dev/null || die "The sudoers rule is invalid."
install -m 440 "$sudoers" /etc/sudoers.d/pis2cr-hcitool
rm -f "$sudoers"
conf_set PIS2CR_USER "$user"
conf_set PIS2CR_HOME "$home"
chmod 644 "$conf"
as_user mkdir -p "$home/.config/pis2cr"
chmod 700 "$home/.config/pis2cr"

step 7 "Starting services"
systemctl daemon-reload
systemctl enable --now pis2cr-ble-interval >/dev/null 2>&1
start_services_if_ready
echo "RESULT installed"
