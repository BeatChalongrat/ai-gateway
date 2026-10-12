#!/usr/bin/env bash
# ops — nothing heavy starts on battery.
#
# The host is a laptop that also runs live. On battery it runs at about half speed (the backend lane
# took 510 s against 321 s on mains; an integration test missed its nine-minute limit), and a deploy
# the battery cuts short leaves live between two images. Until 12 Oct 2026 the rule was a line to read:
# the power was printed before a lane, it said "battery", and the lane ran. A warning that stops
# nothing is a warning nobody reads — so the rule is in the tools. A deploy, an image build and every
# heavy phase of a gate ask this script first, and on battery they do not start.
#
#   bash scripts/ops/power.sh                 what the power is: mains | battery NN% | no battery | unknown
#   bash scripts/ops/power.sh need "<what>"   exit 0 on mains, or on a machine that has no battery;
#                                             on battery, or when the power cannot be read: REFUSED, exit 3
#
# To run one thing on battery anyway — one explicit step, on that one command:
#   RUN_ON_BATTERY=yes bash scripts/deploy-backend.sh …
# The word is "yes" and nothing else, it is read from the command's own environment and from no file,
# and the command says on its first line that it is running on battery because it was told to.
#
# What Windows says is read the way scripts/ops/power-watch.ps1 reads it for the pager. On a Linux host
# it is /sys/class/power_supply; a host with no battery there is on mains.
set -uo pipefail

reading() { # one line: <Online|Offline|Unknown> <1 = has a battery, 0 = none> <charge, percent>
  if command -v powershell > /dev/null 2>&1; then
    powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; \$p = [System.Windows.Forms.SystemInformation]::PowerStatus; \$none = [System.Windows.Forms.BatteryChargeStatus]::NoSystemBattery; \$has = ((\$p.BatteryChargeStatus -band \$none) -ne \$none) -and (\$p.BatteryLifePercent -le 1.0); Write-Output ('{0} {1} {2}' -f \$p.PowerLineStatus, [int]\$has, [int][math]::Round(\$p.BatteryLifePercent * 100))" 2> /dev/null | tr -d '\r' | tail -1
  elif [ -d /sys/class/power_supply ]; then
    local b; b=$(ls -d /sys/class/power_supply/BAT* 2> /dev/null | head -1)
    if [ -z "$b" ]; then echo "Online 0 0"
    elif [ "$(cat "$b/status" 2> /dev/null)" = "Discharging" ]; then echo "Offline 1 $(cat "$b/capacity" 2> /dev/null || echo 0)"
    else echo "Online 1 $(cat "$b/capacity" 2> /dev/null || echo 0)"; fi
  fi
}

state() { # mains | battery NN% | no battery | unknown
  local line has pct r; r=$(reading)
  read -r line has pct <<< "$r"
  case "${has:-}" in
    0) echo "no battery" ;;
    1) case "${line:-}" in
         Online) echo "mains" ;;
         Offline) echo "battery ${pct:-?}%" ;;
         *) echo "unknown" ;;
       esac ;;
    *) echo "unknown" ;;
  esac
}

case "${1:-}" in
  "") state ;;
  need)
    what="${2:?what is about to start}"
    s=$(state)
    case "$s" in
      mains | "no battery") exit 0 ;;
    esac
    [ "$s" = unknown ] && on="the host's power could not be read" || on="the host is on battery (${s#battery })"
    if [ "${RUN_ON_BATTERY:-}" = "yes" ]; then
      echo "ON BATTERY, RUN ANYWAY: $on — $what starts because RUN_ON_BATTERY=yes was given on this command"
      exit 0
    fi
    echo "REFUSED: $on — $what was not started. Plug it in and run it again. To run it on battery anyway, say so on this one command: RUN_ON_BATTERY=yes <the same command>"
    exit 3
    ;;
  *)
    sed -n '2,/^set -uo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
