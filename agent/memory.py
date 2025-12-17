import logging
import json
import time
from typing import List, Dict, Optional, Union
from datetime import datetime, timedelta
import numpy as np
import discord
from .embedding import EmbeddingClient
from core.database import PlaylistDatabase

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.8
MIN_SIMILARITY = 0.8
MAX_CONTEXT_MESSAGES = 20
TIME_WINDOW_HOURS = 24

class SemanticMemoryManager:
    def __init__(self, embedding_client: EmbeddingClient, db: PlaylistDatabase):
        self.embedding_client = embedding_client
        self.db = db
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        try:
            v1 = np.array(vec1)
            v2 = np.array(vec2)
            dot_product = np.dot(v1, v2)
            norm1 = np.linalg.norm(v1)
            norm2 = np.linalg.norm(v2)
            if norm1 == 0 or norm2 == 0:
                return 0.0
            return float(dot_product / (norm1 * norm2))
        except Exception as e:
            logger.error(f"Error computing cosine similarity: {e}")
            return 0.0
    
    def compute_recency_score(self, message_time: datetime, current_time: datetime, time_window_hours: int) -> float:
        try:
            time_diff = (current_time - message_time).total_seconds()
            window_seconds = time_window_hours * 3600
            if time_diff >= window_seconds:
                return 0.0
            recency_ratio = 1.0 - (time_diff / window_seconds)
            return max(0.0, min(1.0, recency_ratio))
        except Exception as e:
            logger.error(f"Error computing recency score: {e}")
            return 0.0
    
    async def get_relevant_messages(
        self,
        current_message: str,
        channel: Optional[discord.TextChannel] = None,
        channel_id: Optional[int] = None,
        time_window_hours: int = TIME_WINDOW_HOURS,
        max_messages: int = MAX_CONTEXT_MESSAGES,
        min_similarity: float = MIN_SIMILARITY,
        limit: int = 50
    ) -> List[Dict]:
        try:
            if not channel and channel_id:
                logger.warning(f"Memory: Channel object not provided, only channel_id {channel_id}. Cannot fetch from Discord.")
                return []
            
            if not channel:
                logger.warning("Memory: No channel provided, cannot fetch messages")
                return []
            
            channel_id = channel.id
            logger.info(f"Memory: Starting retrieval for channel {channel_id}, message: {current_message[:50]}...")
            
            cutoff_time = datetime.utcnow() - timedelta(hours=48)
            logger.debug(f"Memory: Fetching messages from channel {channel_id} (limit: {limit}, after: {cutoff_time})")
            
            discord_messages = []
            async for msg in channel.history(limit=limit, after=cutoff_time, oldest_first=False):
                if msg.author.bot and msg.author.id != channel.guild.me.id if channel.guild else True:
                    continue
                discord_messages.append(msg)
            
            logger.info(f"Memory: Retrieved {len(discord_messages)} messages from Discord channel {channel_id}")
            
            if not discord_messages:
                logger.info(f"Memory: No recent messages found in channel {channel_id}")
                return []
            
            logger.info(f"Memory: Computing embedding for current message")
            current_embedding = await self.embedding_client.embed_query(current_message)
            logger.info(f"Memory: Current message embedding computed (dimension: {len(current_embedding)})")
            current_time = datetime.utcnow()
            
            scored_messages = []
            embeddings_computed = 0
            embeddings_cached = 0
            
            db_embeddings_cache = {}
            if self.db and self.db.pool:
                try:
                    async with self.db.pool.acquire() as conn:
                        rows = await conn.fetch("""
                            SELECT user_message, message_embedding, agent_response
                            FROM chat_history
                            WHERE channel_id = $1
                              AND created_at >= $2
                              AND message_embedding IS NOT NULL
                        """, channel_id, cutoff_time)
                        for row in rows:
                            user_msg = row['user_message']
                            if user_msg:
                                db_embeddings_cache[user_msg] = {
                                    'embedding': json.loads(row['message_embedding']) if row['message_embedding'] else None,
                                    'agent_response': row['agent_response']
                                }
                except Exception as e:
                    logger.debug(f"Memory: Could not fetch DB embeddings: {e}")
            
            bot_id = channel.guild.me.id if channel.guild and channel.guild.me else None
            message_pairs = []
            
            for i, discord_msg in enumerate(discord_messages):
                message_id = discord_msg.id
                user_id = discord_msg.author.id
                user_message = discord_msg.content
                
                if not user_message or not user_message.strip():
                    continue
                
                if user_id == bot_id:
                    continue
                
                agent_response = None
                if i > 0:
                    prev_msg = discord_messages[i - 1]
                    if prev_msg.author.id == bot_id and prev_msg.content and prev_msg.content.strip():
                        agent_response = prev_msg.content
                
                message_pairs.append({
                    'id': message_id,
                    'user_id': user_id,
                    'user_message': user_message,
                    'agent_response': agent_response,
                    'created_at': discord_msg.created_at
                })
            
            logger.debug(f"Memory: Grouped {len(discord_messages)} messages into {len(message_pairs)} user message pairs")
            
            for msg_data in message_pairs:
                message_id = msg_data['id']
                user_id = msg_data['user_id']
                user_message = msg_data['user_message']
                agent_response = msg_data.get('agent_response')
                created_at = msg_data['created_at']
                
                db_entry = db_embeddings_cache.get(user_message)
                stored_embedding = None
                
                if db_entry and db_entry.get('embedding'):
                    stored_embedding = db_entry.get('embedding')
                    if not agent_response and db_entry.get('agent_response'):
                        agent_response = db_entry.get('agent_response')
                    embeddings_cached += 1
                    logger.debug(f"Memory: Using cached embedding for message {message_id}")
                
                if stored_embedding is None:
                    try:
                        full_text = user_message
                        if agent_response:
                            full_text = f"{user_message}\n{agent_response}"
                        logger.debug(f"Memory: Computing missing embedding for message {message_id}")
                        stored_embedding = await self.embedding_client.embed_query(full_text)
                        embeddings_computed += 1
                        logger.debug(f"Memory: Computed embedding for message {message_id}")
                    except Exception as e:
                        logger.error(f"Memory: Error computing embedding for message {message_id}: {e}")
                        continue
                
                similarity = self.cosine_similarity(current_embedding, stored_embedding)
                
                if isinstance(created_at, datetime):
                    msg_created_at = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
                else:
                    msg_created_at = datetime.utcnow()
                
                recency_score = self.compute_recency_score(msg_created_at, current_time, time_window_hours)
                
                combined_score = 0.7 * similarity + 0.3 * recency_score
                
                logger.debug(f"Memory: Message {message_id} - similarity: {similarity:.3f}, recency: {recency_score:.3f}, combined: {combined_score:.3f}")
                
                if similarity >= min_similarity:
                    scored_messages.append({
                        'message': {
                            'id': message_id,
                            'user_id': user_id,
                            'user_message': user_message,
                            'agent_response': agent_response,
                            'created_at': msg_created_at
                        },
                        'score': combined_score,
                        'similarity': similarity,
                        'recency': recency_score
                    })
                    logger.debug(f"Memory: Message {message_id} passed similarity threshold (>= {min_similarity})")
                else:
                    logger.debug(f"Memory: Message {message_id} below similarity threshold ({similarity:.3f} < {min_similarity})")
            
            logger.info(f"Memory: Processed {len(message_pairs)} message pairs from {len(discord_messages)} Discord messages - {embeddings_cached} cached, {embeddings_computed} computed")
            logger.info(f"Memory: {len(scored_messages)} messages passed similarity threshold (>= {min_similarity})")
            
            scored_messages.sort(key=lambda x: x['score'], reverse=True)
            
            top_messages = scored_messages[:max_messages]
            logger.info(f"Memory: Selected top {len(top_messages)} messages (max: {max_messages})")
            
            if top_messages:
                logger.debug(f"Memory: Top message scores - {', '.join([f'{m['score']:.3f}' for m in top_messages[:5]])}")
            
            formatted_messages = []
            for item in top_messages:
                msg = item['message']
                user_id = msg['user_id']
                user_message = msg['user_message']
                agent_response = msg.get('agent_response')
                
                formatted_messages.append({
                    'role': 'user',
                    'content': f"[User {user_id}]: {user_message}"
                })
                
                if agent_response:
                    formatted_messages.append({
                        'role': 'assistant',
                        'content': agent_response
                    })
            
            logger.info(f"Memory: Returning {len(formatted_messages)} formatted messages for context")
            return formatted_messages
            
        except Exception as e:
            logger.error(f"Memory: Error getting relevant messages: {e}")
            return []

