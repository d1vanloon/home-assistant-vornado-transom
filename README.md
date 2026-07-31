# Vornado Transom

A custom [Home Assistant](https://www.home-assistant.io/) integration that lets you discover and control **Vornado Transom** fans registered to your Amazon account.

The fans are exposed through Amazon's Alexa Smart Home service, so this integration signs in with your Amazon credentials and surfaces each discovered fan as a `fan` entity supporting power, speed presets, and airflow direction.

> **Disclaimer:** This is an unofficial, community integration. It is not affiliated with or endorsed by Vornado or Amazon. It relies on the same unofficial Alexa APIs used by the [`aioamazondevices`](https://pypi.org/project/aioamazondevices/) library, which may break without notice if Amazon changes their API.

## Features

- Discovers all Vornado Transom fans linked to your Amazon account.
- One `fan` entity per fan, with:
  - **Power** on/off.
  - **Preset modes** for the fan's speed levels (discovered automatically per device).
  - **Direction** control — `forward` (direct airflow) and `reverse` (exhaust).
- Automatic re-authentication flow when your Amazon session expires.
- Polls the cloud every 60 seconds for state updates.

## Prerequisites

- A Home Assistant installation (2024.8.0 or newer).
- A Vornado Transom fan set up and linked to your Amazon account via the Alexa app.
- Your Amazon account email and password.
- A one-time password (OTP) from a TOTP authenticator app. Currently only OTP-application codes are supported — SMS/email codes are not.

## Installation

### Install via HACS

This integration is published for [HACS (Home Assistant Community Store)](https://hacs.xyz).

1. Click the button below to add this repository to HACS:
   [![Open HACS repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=d1vanloon&repository=home-assistant-vornado-transom&category=integration)

   Or add it manually in HACS → **Custom repositories** with URL `https://github.com/d1vanloon/home-assistant-vornado-transom` and category **Integration**.

2. Search for **Vornado Transom** in HACS and install it.
3. Restart Home Assistant.

### Manual installation

Copy the `custom_components/vornado_transom/` directory into the `custom_components/` folder of your Home Assistant configuration directory, then restart Home Assistant.

## Configuration

1. In Home Assistant go to **Settings** → **Devices & Services** → **Add Integration**.
2. Search for **Vornado Transom** and select it.
3. Enter your Amazon account **email**, **password**, and a current **OTP code** from your authenticator app.
4. Submit. Your fans will be discovered and added automatically.

If your Amazon session expires later, Home Assistant will prompt you to re-authenticate with a fresh password and OTP code.

## Troubleshooting

- **Login fails with `invalid_auth`:** Double-check your email and password, and make sure the OTP code is current (it changes every 30 seconds).
- **`cannot_retrieve_data`:** Amazon temporarily rejected the request. Wait a few minutes and retry.
- Enable debug logging by adding this to your `configuration.yaml`:

  ```yaml
  logger:
    default: info
    logs:
      custom_components.vornado_transom: debug
      aioamazondevices: debug
  ```

## Contributing

Issues and pull requests are welcome at the [issue tracker](https://github.com/d1vanloon/home-assistant-vornado-transom/issues).

## License

This project is released under the MIT License.
