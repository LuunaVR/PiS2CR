#!/bin/bash
# Runs the reference Classic HID backend (built by install.sh under ~/ref, not part of this repo) without its 10-minute test limit.
# Usage: pis2cr-switch.sh run | pair | cleanup
# Reads PIS2CR_USER, PIS2CR_HOME and PIS2CR_SWITCH_MAC from the environment (systemd loads /etc/pis2cr.conf).
set -euo pipefail

adapter=hci0
dropin_dir=/run/systemd/system/bluetooth.service.d
dropin=$dropin_dir/90-pis2cr.conf
store=/var/lib/pcble2gamepad/pairings
colors=(--body-color 828282 --button-color 0F0F0F --left-grip-color 828282 --right-grip-color 828282)

cleanup() {
    rm -f "$dropin"
    rmdir "$dropin_dir" 2>/dev/null || true
    systemctl daemon-reload
    systemctl restart bluetooth
}

if [[ ${1:-} == cleanup ]]; then
    cleanup
    exit 0
fi
[[ ${1:-} == run || ${1:-} == pair ]] || { echo "Usage: $0 run | pair | cleanup" >&2; exit 2; }
mode=$1

: "${PIS2CR_USER:?PIS2CR_USER is not set}"
: "${PIS2CR_HOME:?PIS2CR_HOME is not set}"
owner_uid=$(id -u "$PIS2CR_USER")
ref=$PIS2CR_HOME/ref/pcble2gamepad
app=$ref/build-pro/pcble2gamepad-pro-poc
sdp=$ref/poc/pro-controller.xml

# The backend needs bluetoothd in compat mode with plugins off so it can own the Classic HID PSMs.
start_backend_environment() {
    mkdir -p "$dropin_dir"
    cat > "$dropin" <<'SERVICE'
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --compat --noplugin=*
SERVICE
    systemctl daemon-reload
    systemctl restart bluetooth
    for _ in {1..50}; do
        [[ -d /sys/class/bluetooth/$adapter ]] && systemctl is-active --quiet bluetooth && break
        sleep 0.2
    done
}

if [[ $mode == run ]]; then
    switch_mac=${PIS2CR_SWITCH_MAC:-}
    [[ $switch_mac =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || { echo "PIS2CR_SWITCH_MAC is not set; pair the Switch first." >&2; exit 2; }
    start_backend_environment
    # The backend exits when the Switch is asleep or the link drops; keep retrying.
    while true; do
        "$app" "$adapter" "$sdp" --desktop "$owner_uid" --type pro --reconnect "$switch_mac" "${colors[@]}" || true
        sleep 3
    done
fi

# pair: let the Switch pair with this Pi (Controllers > Change Grip/Order), then copy the new link key to the service user.
marker=$(mktemp)
log=$(mktemp)
trap 'rm -f "$marker" "$log"; kill "${backend:-}" "${tailer:-}" 2>/dev/null || true; cleanup' EXIT
start_backend_environment
# Not in desktop mode, so the backend keeps the link key under owner 0 and quits after its compiled-in 10 minutes.
"$app" "$adapter" "$sdp" --type pro "${colors[@]}" > "$log" 2>&1 &
backend=$!
tail -n +1 -f "$log" --pid="$backend" &
tailer=$!
for _ in $(seq 1 600); do
    grep -q pairing_key_saved "$log" && break
    kill -0 "$backend" 2>/dev/null || break
    sleep 1
done
grep -q pairing_key_saved "$log" || { echo "PAIRING_FAILED: no link key was saved. Check that the Switch is on Controllers > Change Grip/Order." >&2; exit 1; }
sleep 5
kill "$backend" 2>/dev/null || true
wait "$backend" 2>/dev/null || true

file=$(find "$store/0" -name '*.ini' -newer "$marker" -printf '%T@ %p\n' | sort -n | tail -n 1 | cut -d' ' -f2-)
[[ -n $file ]] || { echo "PAIRING_FAILED: the link key file was not found." >&2; exit 1; }
name=$(basename "$file" .ini)
switch_mac=$(echo "${name##*__}" | tr '_' ':' | tr 'a-f' 'A-F')
[[ $switch_mac =~ ^([0-9A-F]{2}:){5}[0-9A-F]{2}$ ]] || { echo "PAIRING_FAILED: unexpected link key file name." >&2; exit 1; }
install -d -m 700 "$store/$owner_uid"
install -m 600 "$file" "$store/$owner_uid/$(basename "$file")"
echo "RESULT switch_mac=$switch_mac"
