# Integration branding (Fellow Stagg EKG)

The integration uses the Fellow Coffee logo. **All icon/logo PNGs for the [home-assistant/brands](https://github.com/home-assistant/brands) repo are in this directory:**

| File | Purpose |
|------|--------|
| `icon.png` | 256×256 icon |
| `icon@2x.png` | 512×512 hDPI icon |
| `logo.png` | Logo |
| `dark_icon.png` | Dark theme icon |
| `dark_icon@2x.png` | Dark theme hDPI icon |
| `dark_logo.png` | Dark theme logo |

- **`branding/icon.svg`** – Same logo (SVG) for HACS and README.

## Where the logo appears

| Place | How it’s shown |
|-------|----------------|
| **HACS** (when you open this addon) | From **info.md** and **README** at repo root (image uses raw GitHub URL). |
| **GitHub** (repo page) | From **README** image (raw URL). You can also set **Settings → General → Social preview** to the logo. |
| **Home Assistant** (Settings → Devices & services, integration card) | **Only** from the [Home Assistant brands repository](https://github.com/home-assistant/brands). There is no other way for custom integrations to set this icon. |

So: **HACS and GitHub can show the logo from this repo.** The **integration card icon in HA** only comes from the brands repo.

## Getting the icon on the HA integration card

Home Assistant loads integration icons from `https://brands.home-assistant.io/{domain}/icon.png`. For that URL to work, the icon must be added to the [home-assistant/brands](https://github.com/home-assistant/brands) repo.

### Steps (all logos are already in this dir)

1. **Fork and clone** [home-assistant/brands](https://github.com/home-assistant/brands).

2. **Copy this folder’s PNGs** into the brands repo as:
   ```
   custom_integrations/fellow_stagg/icon.png
   custom_integrations/fellow_stagg/icon@2x.png
   custom_integrations/fellow_stagg/logo.png
   custom_integrations/fellow_stagg/dark_icon.png
   custom_integrations/fellow_stagg/dark_icon@2x.png
   custom_integrations/fellow_stagg/dark_logo.png
   ```
   (All of these files are already in **this** directory: `custom_components/fellow_stagg/`.)

3. **Open a pull request** to [home-assistant/brands](https://github.com/home-assistant/brands/pulls) with the new `fellow_stagg` folder and the copied files.

After the PR is merged, `https://brands.home-assistant.io/fellow_stagg/icon.png` will work and Home Assistant will show it on the integration card.

See the [brands README](https://github.com/home-assistant/brands/blob/master/README.md) for full image requirements.
