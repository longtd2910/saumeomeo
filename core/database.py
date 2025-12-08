import os
import logging
import asyncio
import asyncpg
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

class PlaylistDatabase:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self, max_retries: int = 10, retry_delay: float = 2.0):
        database_url = os.getenv('DATABASE_URL', 'postgresql://playlist_user:playlist_pass@localhost:5432/playlist_db')
        
        for attempt in range(max_retries):
            try:
                self.pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
                await self.create_tables()
                logger.info("Database connection established")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.warning(f"Failed to connect to database (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                    self.pool = None

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def create_tables(self):
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, url)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_id ON playlists(user_id)
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS versions (
                    id SERIAL PRIMARY KEY,
                    version TEXT NOT NULL,
                    release_note TEXT NOT NULL,
                    announced BOOLEAN DEFAULT FALSE
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS guilds (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL UNIQUE,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_guild_id ON guilds(guild_id)
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS play_log (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_play_log_guild_id ON play_log(guild_id)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_play_log_played_at ON play_log(played_at)
            """)

    async def add_song(self, user_id: int, url: str, title: Optional[str] = None) -> bool:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO playlists (user_id, url, title)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, url) DO NOTHING
                """, user_id, url, title)
                return True
        except Exception as e:
            logger.error(f"Error adding song: {e}")
            return False

    async def get_playlist(self, user_id: int) -> List[Dict]:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return []
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, url, title, added_at
                    FROM playlists
                    WHERE user_id = $1
                    ORDER BY added_at ASC
                """, user_id)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting playlist: {e}")
            return []

    async def remove_song(self, user_id: int, identifier: str) -> bool:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return False
        try:
            async with self.pool.acquire() as conn:
                if identifier.isdigit():
                    result = await conn.execute("""
                        DELETE FROM playlists
                        WHERE user_id = $1 AND id = (
                            SELECT id FROM playlists
                            WHERE user_id = $1
                            ORDER BY added_at ASC
                            LIMIT 1 OFFSET $2
                        )
                    """, user_id, int(identifier) - 1)
                else:
                    pattern = f"%{identifier}%"
                    result = await conn.execute("""
                        DELETE FROM playlists
                        WHERE user_id = $1 AND (url = $2 OR title ILIKE $3)
                        LIMIT 1
                    """, user_id, identifier, pattern)
                return result == "DELETE 1"
        except Exception as e:
            logger.error(f"Error removing song: {e}")
            return False

    async def get_playlist_urls(self, user_id: int) -> List[str]:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return []
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT url
                    FROM playlists
                    WHERE user_id = $1
                    ORDER BY added_at ASC
                """, user_id)
                return [row['url'] for row in rows]
        except Exception as e:
            logger.error(f"Error getting playlist URLs: {e}")
            return []

    async def get_latest_version(self) -> Optional[str]:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return None
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT version
                    FROM versions
                    ORDER BY id DESC
                    LIMIT 1
                """)
                return row['version'] if row else None
        except Exception as e:
            logger.error(f"Error getting latest version: {e}")
            return None

    async def add_version(self, version: str, release_note: str) -> bool:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO versions (version, release_note, announced)
                    VALUES ($1, $2, FALSE)
                """, version, release_note)
                return True
        except Exception as e:
            logger.error(f"Error adding version: {e}")
            return False

    async def add_guild(self, guild_id: int) -> bool:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO guilds (guild_id)
                    VALUES ($1)
                    ON CONFLICT (guild_id) DO NOTHING
                """, guild_id)
                return True
        except Exception as e:
            logger.error(f"Error adding guild: {e}")
            return False

    async def get_all_guilds(self) -> List[int]:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return []
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT guild_id
                    FROM guilds
                """)
                return [row['guild_id'] for row in rows]
        except Exception as e:
            logger.error(f"Error getting all guilds: {e}")
            return []

    async def is_version_announced(self, version: str) -> bool:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return False
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT announced
                    FROM versions
                    WHERE version = $1
                """, version)
                return row['announced'] if row else False
        except Exception as e:
            logger.error(f"Error checking version announcement status: {e}")
            return False

    async def mark_version_announced(self, version: str) -> bool:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE versions
                    SET announced = TRUE
                    WHERE version = $1
                """, version)
                return True
        except Exception as e:
            logger.error(f"Error marking version as announced: {e}")
            return False

    async def get_version_release_note(self, version: str) -> Optional[str]:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return None
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT release_note
                    FROM versions
                    WHERE version = $1
                """, version)
                return row['release_note'] if row else None
        except Exception as e:
            logger.error(f"Error getting version release note: {e}")
            return None

    async def log_played_url(self, guild_id: int, url: str, title: Optional[str] = None) -> bool:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO play_log (guild_id, url, title)
                    VALUES ($1, $2, $3)
                """, guild_id, url, title)
                return True
        except Exception as e:
            logger.error(f"Error logging played URL: {e}")
            return False

    async def get_random_urls_from_history(self, guild_id: int, count: int = 1) -> List[Dict]:
        if not self.pool:
            logger.error("Database pool is not initialized")
            return []
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT url, title
                    FROM (
                        SELECT DISTINCT url, title
                        FROM play_log
                        WHERE guild_id = $1
                    ) AS distinct_urls
                    ORDER BY RANDOM()
                    LIMIT $2
                """, guild_id, count)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting random URLs from history: {e}")
            return []
