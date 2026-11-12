#!/bin/bash
# Finds a Pro Controller 2 in sync mode and bonds it to this Pi. Prints "WAITING", "FOUND <mac>" and "RESULT controller_mac=<mac>".
# Usage: sudo bash deploy/pair-controller.sh [CONTROLLER_MAC]
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

require_root
resolve_user
mac=${1:-}
[[ -z $mac || $mac =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || die "That is not a Bluetooth address."

stop_services pis2cr-relay
trap restore_services EXIT
rfkill unblock bluetooth || true
hciconfig hci0 up 2>/dev/null || true

# The Pro Controller 2 advertises Nintendo's manufacturer id 0x0553 while it waits to pair.
find_controller() {
    local seen=" " device
    bluetoothctl power on >/dev/null 2>&1 || true
    bluetoothctl --timeout "$1" scan on >/dev/null 2>&1 &
    local scanner=$!
    for _ in $(seq 1 "$1"); do
        for device in $(bluetoothctl devices | awk '{print $2}'); do
            [[ $seen == *" $device "* ]] && continue
            seen+="$device "
            if bluetoothctl info "$device" | grep -qi 'ManufacturerData.Key: 0x0553'; then
                kill "$scanner" 2>/dev/null || true
                bluetoothctl scan off >/dev/null 2>&1 || true
                echo "$device"
                return 0
            fi
        done
        sleep 1
    done
    kill "$scanner" 2>/dev/null || true
    return 1
}

if [[ -z $mac ]]; then
    echo "WAITING: hold the sync button on the controller until its lights flash"
    mac=$(find_controller 90) || die "No controller in sync mode was found within 90 seconds."
    mac=${mac^^}
    echo "FOUND $mac"
fi

as_user python3 "$repo/tools/bond_pro2.py" "$mac" 120
conf_set PIS2CR_CONTROLLER_MAC "$mac"
echo "RESULT controller_mac=$mac"
start_services_if_ready
