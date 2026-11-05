#!/usr/bin/env bash
# Explicit local host provisioning. Never invoked by image build or CI.
set -euo pipefail
with_docker=false
with_ssh=false
with_network=false
installer_args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --docker) with_docker=true; shift ;;
    --ssh) with_ssh=true; shift ;;
    --network-manager) with_network=true; shift ;;
    --hdmi) installer_args+=(--hdmi); shift ;;
    --admin-user) installer_args+=(--admin-user "${2:?Missing administrator username}"); shift 2 ;;
    --release) release=${2:?Missing immutable SHA}; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo on the intended target host.' >&2; exit 1; }
[[ "${release:-}" =~ ^[a-f0-9]{40}$ ]] || { echo '--release requires a full commit SHA.' >&2; exit 2; }
# shellcheck source=/dev/null
. /etc/os-release
case "${ID:-} ${ID_LIKE:-}" in
  *debian*|*raspbian*) ;;
  *) echo 'Supported baseline: Raspberry Pi OS / Debian with systemd.' >&2; exit 1 ;;
esac
[ -d /run/systemd/system ] || { echo 'A running systemd host is required.' >&2; exit 1; }
packages=(python3 ca-certificates iproute2 util-linux sudo)
$with_docker && packages+=(docker.io)
$with_ssh && packages+=(openssh-server)
$with_network && packages+=(network-manager)
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
$with_docker && systemctl enable --now docker.service
$with_ssh && systemctl enable --now ssh.service
# Do not replace network config, create users/passwords, grant Docker access,
# install application stacks, or change SSH authentication settings.
exec /usr/bin/python3 "$(dirname "$(realpath "$0")")/install.py" "$release" --root / "${installer_args[@]}"
