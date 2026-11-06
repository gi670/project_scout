# 🔭 GitHub Scout

### Discover interesting GitHub repositories automatically.

GitHub Scout is a Python-powered repository discovery platform that searches GitHub for interesting projects, filters and ranks repositories, stores discoveries locally, and delivers notifications through Discord.

Built to make discovering new technologies, open-source projects, AI tools, and cybersecurity repositories easier.

---

## ✨ Features

- **Automated Repository Discovery** — Search public GitHub repositories using the GitHub REST API.
- **Smart Filtering** — Filter repositories by programming language, stars, topics, and activity.
- **Repository Ranking** — Score and prioritize repositories based on configurable criteria.
- **SQLite Storage** — Persist discovered repositories and notification history locally.
- **Discord Integration** — Deliver repository discoveries through organized Discord channels.
- **Automatic Scheduling** — Run discovery scans at configurable intervals.
- **Duplicate Prevention** — Avoid repeatedly storing or announcing the same repositories.
- **Slash Commands** — Interact with GitHub Scout directly through Discord.

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Core application |
| GitHub REST API | Repository discovery |
| httpx | HTTP requests |
| discord.py | Discord bot integration |
| SQLite | Persistent database |
| APScheduler | Automatic scanning |
| python-dotenv | Environment configuration |

## 🏗️ Architecture

```text
             GitHub REST API
                    |
                    v
          Repository Discovery
                    |
                    v
          Filtering & Ranking
                    |
                    v
              SQLite DB
                    |
                    v
          Discord Notification
                    |
                    v
              GitHub Scout HQ

          APScheduler
          (Periodic scans)
```

## 📁 Project Structure

```text
github-scout/
│
├── app/
│   ├── database/
│   │   ├── config.py
│   │   └── store.py
│   │
│   ├── discovery/
│   │   ├── engine.py
│   │   └── scan_service.py
│   │
│   ├── github/
│   │   └── client.py
│   │
│   ├── notifications/
│   │   ├── discord_bot.py
│   │   └── service.py
│   │
│   ├── scheduling/
│   │   └── scan_scheduler.py
│   │
│   ├── config.py
│   └── main.py
│
├── scripts/
├── tests/
├── data/
├── logs/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/github-scout.git
cd github-scout
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
```

Activate it:

**macOS / Linux**

```bash
source .venv/bin/activate
```

**Windows**

```bash
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure environment variables

Create your `.env` file:

```bash
cp .env.example .env
```

Configure the required values:

```env
DISCORD_TOKEN=your_discord_bot_token
DEV_GUILD_ID=your_discord_server_id

GITHUB_TOKEN=your_optional_github_token

SCAN_ENABLED=true
SCAN_INTERVAL_MINUTES=30
```

Never commit your actual `.env` file or credentials.

### 5. Start GitHub Scout

```bash
python -m app.main
```

The bot connects to Discord, initializes its SQLite database, and starts its configured discovery workflow.

## 🤖 Discord Commands

| Command | Description |
|---|---|
| `/ping` | Check bot connectivity |
| `/status` | View application status |
| `/latest` | View recently discovered repositories |
| `/search` | Search stored repositories |
| `/stats` | View discovery statistics |
| `/rescan` | Trigger a manual discovery scan |

## ⏰ Automatic Scanning

GitHub Scout supports scheduled repository discovery.

Default configuration:

```env
SCAN_ENABLED=true
SCAN_INTERVAL_MINUTES=30
```

The scheduler periodically discovers repositories, processes them through the filtering and ranking engine, stores results in SQLite, and sends eligible Discord notifications.

## 🧪 Testing

Run the test suite:

```bash
python -m pytest
```

Tests cover application configuration, database operations, discovery logic, notification services, and scheduling.

## 🔐 Security

- Keep API tokens and Discord credentials in `.env`.
- Never commit secrets to version control.
- Keep `.env` and local database files out of Git.
- Use environment variables for sensitive configuration.

## 🎯 Project Goals

GitHub Scout aims to simplify discovering interesting open-source projects and help developers explore new technologies and communities.

## 🛣️ Future Improvements

- AI-powered repository summaries.
- Personalized repository recommendations.
- Emerging technology detection.
- Advanced repository quality analysis.
- Web dashboard for discovery analytics.
- Additional filtering and ranking capabilities.

## 👩‍💻 Author

Developed as an independent Python and automation project.

---

**GitHub Scout — Discover. Explore. Build.**

If you find this project interesting, consider giving the repository a ⭐.
