# XRM-1 Installer

Install **XRM-1** automatically using Cloud-Init or manually through the terminal.

## Automatic Installation with Cloud-Init

Paste the following configuration into the **Cloud-Init / User Data** section when creating your server:

```yaml
#cloud-config

runcmd:
  - cd /root
  - wget -q https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh -O install.sh
  - chmod +x install.sh
  - bash install.sh
```

## Manual Installation

Run the following command as the `root` user:

```bash
cd /root && \
wget -q https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh -O install.sh && \
chmod +x install.sh && \
bash install.sh
```

## One-Line Installation

```bash
wget -qO- https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh | bash
```

> [!IMPORTANT]
> Run the installer with `root` privileges on a newly created server.

## Requirements

* Ubuntu or Debian-based server
* Root access
* Active internet connection

## Repository

```text
https://github.com/exirhub/xrm-1
```
