# Sâu Meo Meo (saumeomeo)

A Discord music bot with PostgreSQL-backed playlists, play history, interactive player UI, and mention-based chat. Streams audio from YouTube and other sources via **yt-dlp** and **FFmpeg**, and ships with LangChain agent tooling for natural-language music control and semantic conversation memory.

## Features

- **Voice playback** — Join a voice channel and play URLs, search queries, or personal playlists.
- **Slash commands** — Play, pause, resume, skip, queue management, interactive player, and playlist CRUD.
- **Per-guild queue** — In-memory queue with auto-advance, idle disconnect, and live-updating player embeds.
- **PostgreSQL persistence** — User playlists, server play logs, version announcements, guild registry, and chat history with embeddings.
- **Server random play** — `/random` picks from a guild’s play history.
- **Mention chat** — Tag the bot to chat via an external analyst API (separate from slash-command music control).
- **Semantic memory** — Retrieves relevant past messages (embedding + recency) to enrich LLM context when the agent layer is used.
- **Docker deployment** — `docker compose` runs the bot and Postgres; pushes to `main` deploy on a self-hosted runner.

## Tech stack

| Layer | Tools |
|--------|--------|
| Runtime | Python 3.13+, [uv](https://github.com/astral-sh/uv) |
| Discord | [discord.py](https://github.com/Rapptz/discord.py) (voice) |
| Audio | yt-dlp, FFmpeg, Node.js (EJS remote components) |
| Database | PostgreSQL ([asyncpg](https://github.com/MagicStack/asyncpg)) |
| AI | LangChain, LangGraph tools, OpenAI-compatible chat & embedding APIs |

## Project layout

```
saumeomeo/
├── run.py              # Entry point, version registration, bot startup
├── core/
│   ├── bot.py          # MusicBot cog, listeners, slash commands
│   ├── controller.py   # Playback and playlist business logic
│   ├── audio.py        # Streaming audio sources (yt-dlp)
│   ├── database.py     # PostgreSQL access and schema
│   ├── state.py        # Per-guild queues and player state
│   ├── view.py         # Discord embeds and UI components
│   └── remote_chat.py  # External chat API client
├── agent/
│   ├── llm.py          # LangChain music agent
│   ├── tool.py         # Agent tools (play, skip, pause, …)
│   ├── memory.py       # Semantic context retrieval
│   └── embedding.py    # Embedding client
├── Dockerfile
├── docker-compose.yaml
└── pyproject.toml
```

## Slash commands

| Command | Description |
|---------|-------------|
| `/play` | Play a URL, search query, or `personal` playlist |
| `/skip` | Skip the current track |
| `/pause` / `/resume` | Pause or resume playback |
| `/queue` | View the queue |
| `/clear` | Clear the queue |
| `/stop` | Stop playback |
| `/player` | Show the interactive player with progress |
| `/playlist` | View your saved playlist |
| `/add` / `/remove` | Add or remove tracks from your playlist |
| `/play-playlist` | Play your personal playlist |
| `/random` | Play a random track from this server’s history |

Prefix commands use `!` (see `run.py`); slash commands are synced on startup.

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- **FFmpeg** and **Node.js** (required for yt-dlp EJS components when not using Docker)
- **PostgreSQL** (local or via `docker compose`)
- A **Discord application** with bot token and appropriate intents (including voice and message content if you use mentions)

## Configuration

Create a `.env` file in the project root:

```env
DISCORD_API_KEY=your_discord_bot_token
DATABASE_URL=postgresql://playlist_user:playlist_pass@localhost:5432/playlist_db
HARD_KEY=your_analyst_api_bearer_token
```

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_API_KEY` | Yes | Discord bot token |
| `DATABASE_URL` | No | Postgres connection string (defaults to local `playlist_db`) |
| `HARD_KEY` | For mention chat | Bearer token for the external chat API |

The LangChain chat and embedding clients default to OpenAI-compatible endpoints configured in `agent/llm.py` and `agent/embedding.py`. Point those at your own inference services if you use the agent layer.

## Local development

1. Clone the repository and install dependencies:

   ```bash
   uv sync
   ```

2. Start PostgreSQL (example with Docker Compose):

   ```bash
   docker compose up -d postgres
   ```

3. Set environment variables (see above).

4. Run the bot:

   ```bash
   uv run python run.py
   ```

On startup, the bot connects to the database, creates tables if needed, registers guilds, syncs slash commands, and records a new app version when `VERSION` in `run.py` is greater than the latest stored version.

## Docker

Build and run the full stack (bot + Postgres):

```bash
docker compose up -d --build
```

The bot image installs FFmpeg, Node.js, and dependencies via `uv sync --frozen`, then runs `uv run python run.py`.

## Deployment

Pushes to the `main` branch trigger [`.github/workflows/deploy-docker.yml`](.github/workflows/deploy-docker.yml) on a **self-hosted** runner: checkout, write `.env` from the `ENV` secret, then `docker compose up -d --build`.

## Usage notes

- Join a **voice channel** before using play commands; the bot joins your channel automatically when possible.
- **Mention the bot** in a text channel to start a chat session (requires `HARD_KEY`).
- Playlist and play-log features need a working database connection; the bot logs a warning and continues without DB features if Postgres is unavailable.
- Version release notes are defined in `run.py` (`VERSION`, `CHANGE_NOTE`) and announced once per guild when a new version is detected.

## License

No license file is included in this repository. Add one if you plan to distribute or open-source the project.
