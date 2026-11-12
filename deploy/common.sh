# Shared by install.sh, pair-controller.sh and pair-switch.sh. Source it; the scripts run as root.
conf=/etc/pis2cr.conf

die() { echo "ERROR: $*" >&2; exit 1; }

require_root() { [[ $EUID -eq 0 ]] || die "Run this with sudo."; }

# Sets user, home, uid and repo (the folder that holds tools/ and deploy/).
resolve_user() {
    user=${PIS2CR_USER:-${SUDO_USER:-}}
    [[ -n $user && $user != root ]] || die "Run with sudo from your normal login (not as root directly), or set PIS2CR_USER."
    id "$user" >/dev/null 2>&1 || die "User $user does not exist."
    home=$(getent passwd "$user" | cut -d: -f6)
    uid=$(id -u "$user")
    repo=$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)
}

as_user() { sudo -u "$user" -H "$@"; }

was_running=()
# Stops the given services and remembers which of them were running, for restore_services.
stop_services() {
    local unit
    for unit in "$@"; do
        ! systemctl is-active --quiet "$unit" || was_running+=("$unit")
        systemctl stop "$unit" 2>/dev/null || true
    done
}

restore_services() {
    local unit
    for unit in "${was_running[@]}"; do systemctl start "$unit" 2>/dev/null || true; done
}

conf_get() { sed -n "s/^$1=//p" "$conf" 2>/dev/null | tail -n 1; }

conf_set() {
    touch "$conf"
    if grep -q "^$1=" "$conf"; then sed -i "s|^$1=.*|$1=$2|" "$conf"; else echo "$1=$2" >> "$conf"; fi
}

# Enables and (re)starts the services once both the Switch and the controller are paired.
start_services_if_ready() {
    if [[ -n $(conf_get PIS2CR_SWITCH_MAC) && -n $(conf_get PIS2CR_CONTROLLER_MAC) ]]; then
        systemctl enable pis2cr-switch pis2cr-relay >/dev/null 2>&1
        systemctl restart pis2cr-switch pis2cr-relay
        echo "READY: services started"
    else
        echo "NOT_READY: switch=$([[ -n $(conf_get PIS2CR_SWITCH_MAC) ]] && echo paired || echo missing) controller=$([[ -n $(conf_get PIS2CR_CONTROLLER_MAC) ]] && echo paired || echo missing)"
    fi
}
