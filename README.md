# BMO

[![npm version](https://img.shields.io/npm/v/@aliwey/bmo?color=blue)](https://www.npmjs.com/package/@aliwey/bmo)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-Aliw02%2FBMO-181717?logo=github)](https://github.com/Aliw02/BMO)

**BMO** connects your local OpenCode instance to **Telegram**, a rich **CLI TUI**, and a **webchat** interface — all sharing a single SQLite database and session history.

## Features

- **Telegram Bot** — chat with BMO from anywhere via Telegram
- **CLI TUI** — interactive terminal with rich markdown, session management, agent switching, and `!` shell commands
- **Webchat** — browser-based chat with automatic Cloudflare Tunnel for public access
- **Shared State** — all frontends share one SQLite database and one active session
- **BFP Protocol** — peer-to-peer agent discovery and messaging (A2A-compatible)
- **Self-Evolving** — BMO writes its own code, installs dependencies, and updates itself

## Installation

```bash
npm install -g @aliwey/bmo
```

## Quick Start

```bash
# Setup wizard — links Telegram token and configures settings
bmo init

# Launch Telegram bot + CLI TUI
bmo

# Launch webchat with public Cloudflare Tunnel
bmo web

# Start peer-to-peer relay (BFP)
bmo relay
```

## CLI Shell Commands

In the CLI, prefix any command with `!` to run it as a shell command:

```
!git status
!dir
!npm install
```

BMO sees the output and can act on it.

## Requirements

- Python 3.10+
- Node.js 18+
- OpenCode server (auto-started by BMO)

## Commands

| Command | Description |
|---------|-------------|
| `bmo` | Launch bot + CLI TUI |
| `bmo init` | Configuration wizard |
| `bmo web` | Webchat server + tunnel |
| `bmo relay` | BFP peer relay |
| `bmo --version` | Show version |
| `bmo --update` | Update to latest |

## Storage

All data lives in `~/.bmo/`:
- `.env` — configuration
- `data/bot.db` — SQLite database (sessions, memory, agents)
- `logs/` — log files

## License

MIT © Aliwey
