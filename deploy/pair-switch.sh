#!/bin/bash
# Lets the Switch 2 pair with this Pi as a Pro Controller. Start it, then on the Switch open Controllers > Change Grip/Order.
# Prints "RESULT switch_mac=<mac>" when the link key was saved.
# Usage: sudo bash deploy/pair-switch.sh
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

require_root
resolve_user
[[ -x /usr/local/lib/pis2cr/pis2cr-switch.sh ]] || die "Run deploy/install.sh first."

stop_services pis2cr-relay pis2cr-switch
log=$(mktemp)
trap 'rm -f "$log"; restore_services' EXIT
echo "WAITING: open Controllers > Change Grip/Order on the Switch"
PIS2CR_USER=$user PIS2CR_HOME=$home /usr/local/lib/pis2cr/pis2cr-switch.sh pair 2>&1 | tee "$log"
mac=$(sed -n 's/^RESULT switch_mac=//p' "$log" | tail -n 1)
[[ -n $mac ]] || die "The Switch address was not found."
conf_set PIS2CR_SWITCH_MAC "$mac"
start_services_if_ready
