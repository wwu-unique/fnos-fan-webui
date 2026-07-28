#!/bin/bash
# collect_disk_temps.sh - Collect disk temperatures via smartctl and write to JSON
# Run on FNOS host via systemd timer every 30 seconds
# Writes to /opt/fnos-fan-webui/data/disk-temps.json by default
# (mounted into Docker as /data/disk-temps.json). Override with DATA_DIR=/path.

set -euo pipefail

DATA_DIR="${DATA_DIR:-/opt/fnos-fan-webui/data}"
OUTPUT_FILE="${DATA_DIR}/disk-temps.json"

mkdir -p "$DATA_DIR"

# Collect NVMe temperatures from smartctl
declare -A DISK_TEMPS=()

# Find NVMe devices
for dev in /dev/nvme[0-9]n[0-9]; do
    if [ -b "$dev" ]; then
        # Try smartctl first (most reliable)
        temp=$(smartctl -A "$dev" 2>/dev/null | awk '/^Temperature:/ {print $2; exit}' || true)
        
        # Fallback: smartctl -l sctemp or -i
        if [ -z "$temp" ]; then
            temp=$(smartctl -A "$dev" 2>/dev/null | awk '/^  194  Temperature/ {print $10; exit}' || true)
        fi
        
        # Fallback: smartctl -a and look for Temperature
        if [ -z "$temp" ]; then
            temp=$(smartctl -a "$dev" 2>/dev/null | grep -i "temperature" | grep -oP '\d+' | head -1 || true)
        fi
        
        # Fallback: read from sysfs NVMe
        if [ -z "$temp" ]; then
            nvme_name=$(basename "$dev" | sed 's/n[0-9]$//')
            for hwmon in /sys/class/nvme/nvme*/hwmon*/temp*_input; do
                if [ -f "$hwmon" ]; then
                    sys_temp=$(cat "$hwmon" 2>/dev/null || true)
                    if [ -n "$sys_temp" ] && [ "$sys_temp" -gt 1000 ] 2>/dev/null; then
                        temp=$((sys_temp / 1000))
                        break
                    fi
                fi
            done
        fi
        
        if [ -n "$temp" ]; then
            DISK_TEMPS["$dev"]="$temp"
        fi
    fi
done

# Also check SATA drives
for dev in /dev/sd[a-z]; do
    if [ -b "$dev" ]; then
        temp=$(smartctl -A "$dev" 2>/dev/null | awk '/^  194  Temperature/ {print $10; exit}' || true)
        if [ -z "$temp" ]; then
            temp=$(smartctl -a "$dev" 2>/dev/null | grep -i "temperature" | grep -oP '\d+' | head -1 || true)
        fi
        if [ -n "$temp" ]; then
            DISK_TEMPS["$dev"]="$temp"
        fi
    fi
done

# Build JSON output
json="{"
first=true
for dev in "${!DISK_TEMPS[@]}"; do
    if [ "$first" = true ]; then
        first=false
    else
        json+=","
    fi
    # Use device name without /dev/ prefix as key
    devname=$(basename "$dev")
    json+="\"${devname}\":${DISK_TEMPS[$dev]}"
done
json+='}'

# Write atomically (write to temp file, then move)
tmp_file=$(mktemp "${DATA_DIR}/.disk-temps.XXXXXX")
echo "$json" > "$tmp_file"
mv "$tmp_file" "$OUTPUT_FILE"

# Log for debugging (optional, keep last 100 entries)
LOG_FILE="${DATA_DIR}/disk-temp-log.jsonl"
timestamp=$(date +%s)
echo "{\"ts\":${timestamp},\"temps\":${json}}" >> "$LOG_FILE"

# Trim log to last 100 entries
if [ -f "$LOG_FILE" ]; then
    tail -n 100 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
