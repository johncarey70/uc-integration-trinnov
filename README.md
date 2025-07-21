# Trinnov Altitude Integration for Unfolded Circle Remotes

## ⚠️ Disclaimer ⚠️


This software may contain bugs that could affect system stability. Please use it at your own risk!
This integration driver allows control of a **Trinnov Altitude Processor** device. A media player and remote entity are exposed to the UC Remotes core.

Supported **media player** attributes:

- State (on, off, unknown)

Supported **media player** commands:

- Power on
- Power off
- Toggle power

Supported **remote** UI:

- Power on
- Power off
- Toggle power
- Directional pad
- Numeric keypad

## Installation

### Run on the remote as a custom integration driver

#### 1 - Download Integration Driver
Download the uc-intg-trinnov-x.x.x-aarch64.tar.gz archive in the assets section from the [latest release](https://github.com/johncarey70/uc-integration-trinnov/releases/latest).

#### 2 - Upload & Installation
Upload in the Web Configurator
Go to Integrations in the top menu. On the top right click on Add new/Install custom and choose the downloaded tar.gz file.

#### 3 - Configuration
Click on the Integration to run setup. The player should be found automatically, if not use the manual setup.

#### 4 - Updating
First remove the existing version by clicking the delete icon on the integration, this needs to be done twice. The first time deletes the configuration, the second time fully removes it. Then repeat the above steps.

### Run on a local server
The are instructions already provided by unfolded circle in their included integrations, there is a docker-compile.sh in the repository that is used to build the included tar file.


## License

Licensed under the [**Mozilla Public License 2.0**](https://choosealicense.com/licenses/mpl-2.0/).  
See [LICENSE](LICENSE) for more details.
