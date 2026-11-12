# Installing, updating and rebuilding the Pi

`PiS2CR.exe` runs all of this for you (see the README). This page says what the scripts do, for repairs or a manual install.

## What `deploy/install.sh` does

Run as `sudo bash ~/pis2cr/deploy/install.sh` with the repo copied to `~/pis2cr` (it needs `tools/` and `deploy/` there). It is safe to run again; pairings and settings are kept.

1. Installs packages: `git meson ninja-build libglib2.0-dev libbluetooth-dev libjson-glib-dev bluez python3 openssl rfkill sudo`.
2. Unblocks Bluetooth and checks that `hci0` exists.
3. Clones the reference backend [wozt/pcble2gamepad](https://github.com/wozt/pcble2gamepad) (GPL-3.0, a separate program) to `~/ref/pcble2gamepad`, pinned to one commit.
4. Applies `deploy/backend-imu-battery.patch` (gyro and battery passthrough).
5. Builds the backend with meson and ninja.
6. Installs `/usr/local/lib/pis2cr/pis2cr-switch.sh`, the systemd units (with your username and folder filled in), the sudoers rule for the link-speed request, the rule that disables the USB Bluetooth driver, and `/etc/pis2cr.conf`.
7. Enables the services. They start once both pairings are done.

`/etc/pis2cr.conf` holds `PIS2CR_USER`, `PIS2CR_HOME`, `PIS2CR_SWITCH_MAC` and `PIS2CR_CONTROLLER_MAC`. There are no secrets in it.

## Pairing scripts

- `deploy/pair-switch.sh`: runs the backend so the Switch 2 can pair (Controllers, Change Grip/Order), keeps the link key for your user, and stores the Switch's address.
- `deploy/pair-controller.sh [MAC]`: finds a Pro Controller 2 in sync mode (Bluetooth manufacturer id `0x0553`), bonds it with `tools/bond_pro2.py`, and stores its address.

Both stop the relay while they run and start it again afterwards.

## What is where on the Pi

| Item | Location |
|---|---|
| Relay, dashboard, tools | `~/pis2cr/tools` |
| Scripts, unit templates | `~/pis2cr/deploy` |
| Backend source and build | `~/ref/pcble2gamepad` |
| Switch link key (secret) | `/var/lib/pcble2gamepad/pairings/<uid>/` |
| Controller bond (secret), chat, overlay and Twitch settings (the Twitch file holds a token) | `~/.config/pis2cr` |
| Addresses of the Switch and controller | `/etc/pis2cr.conf` |
| Which version is installed | `~/pis2cr/.bundle-hash` (written by the setup page) |

## After losing the SD card

Flash again and run the setup page. The Switch link key and the controller bond are not stored in this repo, so pair both again, and connect Twitch again from the dashboard. Twitch settings come back as defaults.

## Known limits

- Tested on a Raspberry Pi 4 Model B with Raspberry Pi OS Lite 64-bit (Debian 13), using the built-in Bluetooth. A USB Bluetooth dongle is not supported (the installer disables the USB Bluetooth driver).
- The first install on a blank card and the two pairing scripts have not been run end to end yet. The install script was run on the working Pi as an update, and the controller scan was run once with no controller in sync mode.
