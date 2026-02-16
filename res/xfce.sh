#!/bin/bash
set -e

# Disable screensaver and power management
xset -dpms &
xset s noblank &
xset s 0 0 &
xset s off &

# Start xfce
/usr/bin/startxfce4 --replace > "$HOME"/xfce.log &
sleep 2
# xfce4-panel may not be available on some setups; avoid noisy DBus errors.
if pgrep -x xfce4-panel > /dev/null 2>&1; then
    xfce4-panel --quit > /dev/null 2>&1 || true
fi
cat "$HOME"/xfce.log
