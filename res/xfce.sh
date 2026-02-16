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
xfce4-panel --quit &
cat "$HOME"/xfce.log