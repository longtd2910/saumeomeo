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
