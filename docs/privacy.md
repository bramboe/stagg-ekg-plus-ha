# Privacy: what the kettle sends to Fellow, and how to stop it

This page describes data the **kettle's own firmware** sends to Fellow, independently of Home
Assistant. The integration adds no telemetry of its own and never forwards anything to Fellow.

The findings below come from analysing the Fellow Stagg EKG Pro firmware image **1.2.26**
(`ekg-firmware`, ESP-IDF v4.4.8). They describe that firmware version; other versions may differ.

## What the kettle contacts

All cloud traffic goes to a single host:

```
qhderznbzup.firmware.fellowproducts.com   (HTTPS, port 443)
```

Paths observed in the firmware:

| Path | Purpose |
|------|---------|
| `/get_ota_device_firmware` | Firmware-update download (RSA-PSS signed; built-in IoT config) |
| `/set_firmware_time` | Clock/time sync from Fellow's server |
| `/send_metrics` | **Usage metrics upload (per brew session)** |
| `/confirm_firmware_install` | Confirms an installed update |

Every request carries identity headers: a per-device id (from the chip's eFuse UUID), the MAC
address, the firmware version (`fwv`), an `ota-enabled` flag and a `region` (derived from the
display language).

## What the metrics contain

Per brew session the kettle POSTs a JSON record to `/send_metrics`. The fields in 1.2.26 are:

```
device_name, fw_version, mode, target_temp_c, time_to_relay_ms, time_to_target_ms,
overshoot_c, triac_temp_c, temp_noise_c, calibration_c, altitude_raw, no_water_events,
session_duration_ms, uptime_s, heap_free, heap_min_free, heap_largest_block,
reset_reason, wifi_rssi, start_temp_c, volume_ml, fast_rise_max_c, fast_rise_raw_max_c
```

This is telemetry about the device and your brewing (including an estimated water **volume** and
Wi-Fi **signal strength**), tied to your kettle's id and MAC. There is **no firmware setting and no
console command to disable it** — the metrics upload is unconditional whenever the kettle has
internet. `otadis` (the "Auto update" / Block auto-update setting) only suppresses the
firmware-update check, not the metrics.

## Why the integration cannot block it

The kettle opens these HTTPS connections itself, from its own network stack, directly to Fellow.
Home Assistant talks to the kettle over Bluetooth or the kettle's local HTTP interface — it is not a
router or proxy and is not in the path of the kettle's outbound traffic, so it has no way to
intercept or drop it. A checkbox in the integration therefore cannot, by itself, stop the metrics.

## How to actually stop it

Two approaches genuinely work:

### 1. Bluetooth-only (no internet path)

If the kettle has no Wi-Fi, it has nowhere to send data. Use the integration in **Bluetooth** mode
and don't provision Wi-Fi, or clear any configured Wi-Fi with **Configure → Restore factory
settings**. You keep full local control over Bluetooth; nothing can reach Fellow.

### 2. Block the host at the network level

Keep the kettle on Wi-Fi but stop it reaching Fellow:

- **Pi-hole / AdGuard Home:** add `qhderznbzup.firmware.fellowproducts.com` to the blocklist
  (or block the whole `fellowproducts.com` domain). Blocking the parent domain also stops
  `*.firmware.fellowproducts.com` sub-hosts if Fellow rotates the name.
- **Router / firewall:** block outbound traffic from the kettle's IP (or MAC) to that host, or
  give the kettle a DNS server that sinkholes it. Reserve a static DHCP lease for the kettle first
  so the rule stays put.
- **VLAN:** put the kettle on an IoT VLAN with no internet route; Home Assistant still reaches it
  locally.

Blocking the host also stops firmware updates and the time sync. The integration's **Sync clock**
switch keeps the kettle's clock correct without Fellow.

## The "Share data with Fellow" option

**Configure → Polling → Share data with Fellow** (default **off**) is honest about the above:

- **Off (default):** the integration keeps the firmware-update check disabled (`otadis`) over
  whichever console the entry uses (Wi-Fi HTTP CLI or the 1.2.26 Bluetooth console), and re-asserts
  it after a reboot/update. When it sees the kettle on Wi-Fi it shows a one-time notice reminding you
  that the metrics still need a network block or Bluetooth-only use.
- **On:** the integration does not touch `otadis`; use the **Block auto-update** switch for manual
  control.

It is deliberately **not** presented as "blocks metrics", because it cannot.

## Local interface security

Separately from Fellow telemetry: the kettle's local HTTP interface has **no authentication**.
On the local network, anyone can:

- send commands to `/cli` (while the CLI accepts them),
- upload firmware to `/uploadfw`,
- call raw hardware-test endpoints such as `/htest?pwm=<0-100>&target=<C>`, which can switch the
  heating element on directly, and `/hperiod`.

Treat the kettle as an untrusted IoT device: put it on a trusted/IoT VLAN or guest network, or use
Bluetooth-only mode. The integration never exposes these raw endpoints — `send_cli` is restricted to
a fixed read-only diagnostic allowlist — but the kettle answers them regardless of the integration.
