#!/usr/bin/env bash
# Register only SIKA1's management runner. Never accept or print a chat token.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run on SIKA1 as root.'; exit 1; }
[ "$(hostname | tr '[:lower:]' '[:upper:]')" = SIKA1 ] || { echo 'Wrong host.'; exit 1; }
[ "$(uname -m)" = aarch64 ] || { echo 'Expected Linux ARM64.'; exit 1; }
/usr/sbin/sshd -t
id sika1 >/dev/null
for command in curl tar sha256sum python3 runuser docker; do command -v "$command" >/dev/null; done
docker info >/dev/null
getent group docker >/dev/null
usermod -aG docker sika1
runuser -u sika1 -- docker info >/dev/null
runner=/home/sika1/actions-runner-th-server-2
[ ! -L "$runner" ] || { echo 'Runner directory must not be a symlink.'; exit 1; }
if [ -f "$runner/.runner" ]; then
  python3 - "$runner/.runner" <<'PY'
import json,sys
value=json.load(open(sys.argv[1],encoding='utf-8-sig'))
assert value.get('agentName')=='th-server-2','Existing runner has a different name'
assert value.get('gitHubUrl','').rstrip('/')=='https://github.com/TuanPham0411/TH-RPI.Server','Existing runner belongs to another repository'
PY
else
  if [ -d "$runner" ] && [ -n "$(find "$runner" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo 'Runner directory is not empty; inspect it before retrying.'; exit 1
  fi
  archive=$(mktemp /tmp/th-server-2-runner.XXXXXX.tar.gz)
  trap 'rm -f -- "$archive"; unset runner_token' EXIT
  curl --fail --location --silent --show-error --retry 3 --max-time 600 https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-arm64-2.338.2.tar.gz -o "$archive"
  printf '%s  %s\n' 670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945 "$archive" | sha256sum --check --status
  install -d -m 0750 -o sika1 -g "$(id -gn sika1)" "$runner"
  tar --extract --gzip --no-same-owner --file "$archive" --directory "$runner"
  chown -R sika1:"$(id -gn sika1)" "$runner"
  rm -f -- "$archive"
  cd "$runner"
  if ! runuser -u sika1 -- ./config.sh --help >/dev/null 2>&1; then
    ./bin/installdependencies.sh
  fi
  printf 'Paste the GitHub runner registration token here (hidden): '
  IFS= read -r -s runner_token </dev/tty
  printf '\n'
  [ -n "$runner_token" ] || { echo 'Empty token.'; exit 1; }
  runuser -u sika1 -- ./config.sh --url https://github.com/TuanPham0411/TH-RPI.Server --token "$runner_token" --name th-server-2 --no-default-labels --labels th-server-2 --work _work --unattended
  unset runner_token
fi
cd "$runner"
if [ ! -f .service ]; then ./svc.sh install sika1; fi
./svc.sh start
./svc.sh status
systemctl is-active ssh NetworkManager tailscaled cloudflared
printf '\nRunner registered as th-server-2 with the dedicated th-server-2 label.\n'
