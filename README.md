# Trinnov Altitude Integration for Unfolded Circle Remote

![Release](https://img.shields.io/github/v/release/johncarey70/uc-integration-trinnov)
![Downloads](https://img.shields.io/github/downloads/johncarey70/uc-integration-trinnov/latest/total)
![License](https://img.shields.io/github/license/johncarey70/uc-integration-trinnov)
![Last Commit](https://img.shields.io/github/last-commit/johncarey70/uc-integration-trinnov)

[![Download Driver](https://img.shields.io/badge/Download-Latest%20Driver-blue?style=for-the-badge)](https://github.com/johncarey70/uc-integration-trinnov/releases/latest)

---

## Overview

This project provides an **Unfolded Circle Remote integration driver** for **Trinnov Altitude processors**.

The integration exposes the processor to the UC Remote system as a **media player** and **remote entity**, allowing control of inputs, listening modes, and power state while also providing useful system telemetry.

Supported processors include:

- Trinnov **Altitude 16**
- Trinnov **Altitude 32**
- Trinnov **Altitude CI**

The driver automatically discovers compatible processors on the network using **mDNS**.

Discovery is required because the integration retrieves device parameters (such as the processor identifier and MAC address) during the discovery process.

---

## ⚠️ Disclaimer ⚠️

This software may contain bugs that could affect system stability.  
Please use it at your own risk.

---

## Supported Features

### Media Player

#### Attributes

- State (on, off, unknown)

#### Commands

- Power on
- Power off
- Toggle power
- Input select
- Sound mode select

---

### Remote UI

The remote entity exposes a basic control interface including:

- Power on
- Power off
- Toggle power
- Directional pad
- Numeric keypad

**Note**

`send_command` is not implemented for the remote entity.  
The **media_player entity should be used in Activities** for command execution.

---

### Sensors

The integration exposes several system sensors:

- Audio Sync
- Codec
- Upmixer
- Mute Status
- Remapping Mode
- Sample Rate (kHz)
- Volume (dB)

---

### Select Entities

Selectable configuration options:

- Source
- Presets
- Listening Format
- Remapping Mode

---

## Requirements

- Trinnov **Altitude processor** on the same network
- **Unfolded Circle Remote** (R3 or compatible core)
- Network allowing **mDNS discovery**
- Access to the UC Web Configurator

---

## Installation

### Download the Integration Driver

Download the archive from the latest release:

https://github.com/johncarey70/uc-integration-trinnov/releases/latest

File format:

```
uc-intg-trinnov-x.x.x-aarch64.tar.gz
```

---

### Upload and Install

1. Open the **Web Configurator**
2. Navigate to **Integrations**
3. Click **Add new -> Install custom**
4. Upload the downloaded `.tar.gz`

---

### Configuration

Start the integration setup.

The processor should appear automatically via **network discovery (mDNS)**.

Manual configuration is not supported because the integration requires device parameters obtained during the discovery process.

---

### Updating

Updating requires removing the previous driver first.

1. Delete the integration (removes configuration)
2. Delete it again to fully remove the driver
3. Upload the new version
4. Run setup again

---

## Running on a Local Server

Instructions for running integrations externally are already provided by **Unfolded Circle** within their included integrations.

This repository includes a helper script:

```
docker-compile.sh
```

which builds the integration archive used by the remote.

---

## Troubleshooting

### Device not discovered

- Ensure the processor and remote are on the **same network/subnet**
- Verify **mDNS traffic is not blocked**
- Check that multicast filtering or VLAN isolation is not enabled
- Confirm the Trinnov processor is powered on

### Connection issues

- Restart the integration
- Verify the processor API port **44100** is reachable
- Confirm firewall rules allow communication

---

## License

Licensed under the **Mozilla Public License 2.0**

https://choosealicense.com/licenses/mpl-2.0/

See the [LICENSE](LICENSE) file for details.