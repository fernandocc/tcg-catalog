#!/usr/bin/env bash
# TCG Catalog installer for Linux and macOS.
# Run from the app folder:   bash install.sh      (running from source; most people should use the app from the Releases page)
set -u
cd "$(dirname "$0")" || exit 1
bold=$(tput bold 2>/dev/null || true); normal=$(tput sgr0 2>/dev/null || true)
step() { echo; echo "${bold}==> $*${normal}"; }
fail() { echo; echo "${bold}Installation stopped:${normal} $*"; echo; read -rp "Press Enter to close..." _ 2>/dev/null || true; exit 1; }

echo "${bold}TCG Catalog -- installer${normal}"

step "Looking for Python 3.10 or newer"
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    fail "Python 3.10+ is not installed. Download it from https://www.python.org/downloads/ (the big yellow button), install it, then run this installer again."
  else
    fail "Python 3.10+ is not installed. On Ubuntu/Debian/Pop!_OS run:  sudo apt install python3 python3-venv   then run this installer again."
  fi
fi
echo "    using $($PY --version)"

step "Creating the app's private Python environment (venv)"
if [ ! -x venv/bin/python ]; then
  "$PY" -m venv venv || fail "Couldn't create the environment. On Ubuntu/Debian/Pop!_OS run:  sudo apt install python3-venv   then try again."
fi

step "Installing the libraries the app needs (a few minutes the first time)"
venv/bin/python -m pip install --upgrade pip --quiet || true
venv/bin/python -m pip install -r requirements.txt --quiet || fail "Downloading libraries failed. Check your internet connection and run the installer again."

step "Setting up the card database and desktop shortcut"
venv/bin/python setup_helper.py || fail "Setup failed (see the message above)."
chmod +x launch_app.sh

# Linux firewall: the phone has to reach this computer on port 8443
if [ "$(uname)" = "Linux" ] && command -v ufw >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null && [ -t 0 ]; then
  step "Firewall"
  echo "    Your firewall (ufw) is on. The phone needs to reach this computer on port 8443."
  read -rp "    Open port 8443 for your home network now? You'll be asked for your password. [Y/n] " ans
  if [ -z "$ans" ] || [[ "$ans" =~ ^[Yy] ]]; then
    for net in 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12; do sudo ufw allow from "$net" to any port 8443 proto tcp >/dev/null; done && echo "    Done."
  else
    echo "    Skipped. If the phone can't connect later, see Help -> Troubleshooting in the app."
  fi
fi

echo
echo "${bold}All set!${normal} Open ${bold}TCG Catalog${normal} from your desktop (or run ./launch_app.sh)."
if [ "${TCG_NO_LAUNCH:-0}" != "1" ]; then
  echo "Starting it now..."
  venv/bin/python launch.py
fi
