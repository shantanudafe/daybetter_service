# Daybetter Service for Home Assistant

[![hacs][hacs-badge]][hacs-url]
[![GitHub Release][releases-badge]][releases-url]

Custom integration for controlling Daybetter devices in Home Assistant.

## About

This custom component allows Home Assistant to communicate with Daybetter devices using the official Daybetter API. It provides control and monitoring capabilities for your Daybetter-compatible smart devices.

## Prerequisites

Before installing this integration, you need to:

1. Have a Daybetter account
2. Register your devices with the Daybetter services
3. Obtain API credentials if required

## Installation

### Using HACS (Recommended)

1. Install [HACS](https://hacs.xyz/docs/setup/download/)
2. Add this repository as a custom repository in HACS
3. Search for "Daybetter Services" in the Integrations section
4. Install the integration
5. Restart Home Assistant

### Manual Installation

1. Download the `custom_components/daybetter_services` folder
2. Copy it to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to Settings > Devices & Services
2. Click "Add Integration"
3. Search for "Daybetter Services"
4. Enter your Daybetter credentials if required
5. Complete the setup process

## History & statistics (温湿度、电量曲线)

Temperature, humidity, and battery sensors use `state_class: measurement` and **`force_update`** so Home Assistant’s **Recorder** can store a point on each poll (~5 minutes). History and long‑term statistics appear from the moment the entity first reports a numeric state—there is no retroactive data from the DayBetter cloud inside HA.

Ensure **Recorder** is enabled and you have not excluded `sensor.daybetter_services*` (or the entity) in `configuration.yaml`. If you previously had Recorder/database issues, fix SQLite/recorder first.

## Support

If you encounter any issues or have questions:
1. Check the [documentation](https://github.com/THDayBetter/daybetter_service/wiki)
2. Open an [issue](https://github.com/THDayBetter/daybetter_service/issues) on GitHub

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://github.com/custom-components/hacs
[releases-badge]: https://img.shields.io/github/release/THDayBetter/daybetter_service.svg
[releases-url]: https://github.com/THDayBetter/daybetter_service/releases