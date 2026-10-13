# Downloading firmware from Fellow with your kettle's own identity

How the Fellow Stagg EKG Pro (firmware 1.2.26) fetches firmware from Fellow's server, and how to
replicate that request **for a kettle you own** so you can keep a local copy/mirror. This is
reverse-engineered from the firmware image by static disassembly; it has **not** been run against the
live server here. Confirm each step against your own device.

> You already have the integration's local firmware tools (`/uploadfw`, Lock firmware, Upload
> firmware). This page is only about *downloading* an image from Fellow using the kettle's own
> headers — e.g. to archive the version your kettle runs, or to fetch a specific version to host
> locally and flash with `httpfw`/upload.

## The request the kettle makes

```
GET https://qhderznbzup.firmware.fellowproducts.com/get_ota_device_firmware
```

over TLS. The `set_identity_headers` function (`0x400d8e…` in 1.2.26) sets these HTTP headers — the
field names are the literal strings used in the firmware's `esp_http_client_set_header` calls:

| header | value | notes |
|--------|-------|-------|
| `id` | the kettle's 128-bit device id, hex-encoded (~32 chars) | per-device, in eFuse — see below |
| `mac` | Wi-Fi STA MAC, lower-case `aa:bb:cc:dd:ee:ff` | on the device/your router |
| `fwv` | running firmware version, e.g. `1.2.26` | lower a version here to be offered an update |
| `region` | string derived from the display-language setting | e.g. `unknown`; easy to try |
| `ota-enabled` | `true` / `false` | the `otadis` setting; not an auth field |

There is **no shared secret**. The firmware's self-test logs mention a `key` (`HTTP Test #0 … full
key`), but the real download path (`set_identity_headers`) sends only `id`+`mac`(+`fwv`/`region`).
The server authenticates your kettle on **`id` + `mac`** — both are your own device's identifiers.

A non-200 response means, per the firmware's own log: *auth failure / device not registered /
firmware not found*.

## Getting your kettle's `id`

The `id` is a random 128-bit value generated on first boot and burned into an **eFuse user block**
(firmware logs `EFUSE EMPTY, WRITE NEW VALUE` → `uuid generated` → `UUID WRITTEN SUCCESSFULLY`). It
is **not** the MAC, not derivable, and **not exposed over Bluetooth or the local HTTP interface**
(the BLE info characteristics return ip/ssid/name/mac/firmware only). So you read it over the
kettle's **serial/UART** port (USB-TTL at 115200 baud, 3.3 V). Two ways:

### Easiest — watch the serial log (gives the exact header string)

On every OTA check / metrics upload the firmware prints the exact values it sends:

```
send hdr id=<your-id>
send hdr mac=<your-mac>
```

Connect a USB-serial adapter to the kettle's UART, open a monitor (`idf.py monitor`, `pyserial`,
`screen /dev/ttyUSB0 115200`), and either wait for an update cycle or trigger one from the kettle's
console (`httptest`, or `httpfw` against a dummy URL). Copy the `id` exactly as logged — this avoids
any guesswork about hex case/format.

### Alternative — read the eFuse directly

```
espefuse.py --port /dev/ttyUSB0 summary
# or dump the user block(s):
espefuse.py --port /dev/ttyUSB0 dump
```

Look for a non-zero 16-byte user block (typically `BLOCK3`/user data). Hex-encode those 16 bytes to
form the `id`. Caveat: if Fellow **read-protected** the block, `espefuse` shows it as unreadable —
then use the serial-log method above instead.

## Downloading

Once you have your `id`:

```bash
curl -v --output fw.bin \
  -H "id: <your-id>" \
  -H "mac: aa:bb:cc:dd:ee:ff" \
  -H "fwv: 1.2.26" \
  -H "region: unknown" \
  -H "ota-enabled: true" \
  https://qhderznbzup.firmware.fellowproducts.com/get_ota_device_firmware
```

- To be offered a **newer** build, set `fwv` to a lower version than the latest.
- Validate what you receive is an ESP32 app image before trusting it: first byte `0xE9`, and the app
  descriptor magic `0xABCD5432` at offset `0x20` with project name `ekg-firmware`
  (the integration's `parse_esp_app_image` does exactly this).
- The automatic update path also fetches a signed config blob (`{"fwp","fwurl","fwcert"}`, RSA-PSS
  verified) before downloading; that governs the *auto* update. Fetching the `.bin` yourself with the
  headers above is the device-authenticated download, independent of that signed config.

## Hosting it locally and flashing

- Host `fw.bin` on any local HTTP server and install it over the kettle's console:
  `httpfw http://<your-host>:<port>/fw.bin` (via `/cli?cmd=…` or the BLE B6 console).
- Or push it straight from Home Assistant with **Configure → Upload firmware** / the
  `fellow_stagg.install_firmware` service (uses `/uploadfw`).

See also [firmware-1.2.26-analysis.md](firmware-1.2.26-analysis.md) and [privacy.md](privacy.md).

## Caveats

- Reverse-engineered, not tested against the live server from here. One `curl` with your real `id`
  confirms it.
- Use your **own** kettle's `id`/`mac`. Using another device's identifiers impersonates that device.
- A wrong/modified image can brick the kettle; recovery is not guaranteed. Keep the known-good image.
