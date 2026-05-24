# Hermes Agent LAN deployment runbook

Goal: deploy this working tree to another computer on the same local network.

## Target prerequisites

- SSH enabled on the target computer.
  - macOS: System Settings -> General -> Sharing -> Remote Login.
  - Linux: install/start `openssh-server`.
- Target can reach the internet for dependency install.
- Current machine can SSH into the target as `user@ip`.

## Current machine

Current LAN IP observed during setup:

```bash
10.142.205.116
```

## Safe default deployment

This copies only the repo and installs dependencies on the target. It does not
copy Weixin credentials and does not start a remote gateway.

```bash
cd /Users/champion/Documents/develop/hermes-agent
scripts/lan_deploy.sh user@target-ip ~/develop/hermes-agent
```

## Copy Hermes state without taking over Weixin

Use this when the target should have model config, skills, plugins, and existing
runtime settings, but the current machine will continue receiving Weixin DMs.

```bash
scripts/lan_deploy.sh --copy-state user@target-ip ~/develop/hermes-agent
```

## Remote machine takes over Weixin DM

Use this when the target machine should become the active Weixin DM bot host.
The deploy script stops the local gateway first to release the iLink token lock.

```bash
scripts/lan_deploy.sh --takeover-weixin --start-gateway user@target-ip ~/develop/hermes-agent
```

Do not run local and remote Weixin gateways with the same token at the same
time. iLink tokens are single-active-session style credentials.

## Expose remote dashboard to LAN

```bash
scripts/lan_deploy.sh --copy-state --start-dashboard-lan user@target-ip ~/develop/hermes-agent
```

Then open:

```text
http://<target-lan-ip>:9119
```

This uses `hermes dashboard --host 0.0.0.0 --insecure`, which exposes config and
API-key management to the LAN. Only use it on a trusted private network.

## Verify on target

```bash
ssh user@target-ip 'cd ~/develop/hermes-agent && ./.venv/bin/hermes --version'
ssh user@target-ip 'cd ~/develop/hermes-agent && ./.venv/bin/hermes gateway status'
```

## If SSH is not enabled

Enable Remote Login first, then get the target IP:

```bash
ifconfig | awk '/inet / && $2 !~ /^127/ {print $2}'
```

