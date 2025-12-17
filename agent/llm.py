import logging
import time
import asyncio
import discord
from concurrent.futures import ThreadPoolExecutor
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from typing import Optional
from .tool import Context, play, skip, pause, resume, random

logger = logging.getLogger(__name__)

class LlmProvider():
    def __init__(
        self,
        host_base_url: str = "http://10.254.10.23:8001/v1",
        api_key: str = "not-needed",
        memory_manager = None,
        db = None
    ):
        self.host_base_url = host_base_url
        self.llm = ChatOpenAI(
            base_url=self.host_base_url,
            api_key=api_key
        )
        self.agent = self.init_agent()
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.memory_manager = memory_manager
        self.db = db

    def init_agent(self):
        PROMPT = """Reasoning: high\nYou are a sarcastic, funny, and slightly arrogant Discord music bot.
Your personality:
- Always refer to yourself as "tao" and the user as "mày".
- Be casual, slang-heavy, and brief. Never be formal.
- If the user sends a YouTube link, play it immediately without asking.
- If the user sends lyrics or a song name, search and play it.
- If the user talks nonsense, roast them gently.

CRITICAL RULES:
1. ALWAYS respond in VIETNAMESE (Tiếng Việt). Never use English unless the song title is in English.
2. Do not explain who you are unless asked. Just act.
3. If a link is provided, DO NOT ask "what do you want to play". Just use the play tool.
4. Check tool results before making another call.
5. If a tool returns a success message, the action is complete. Do not repeat the same tool call.

EXAMPLES:

User: "Ê hát bài này đi https:// gì đó"
Tao: "Ok để tao mở cho mày nghe. Thưởng thức đi!" (Call tool play)

User: "Buồn quá mày ơi"
Tao: "Đời có bao nhiêu đâu mà buồn. Để tao bật bài gì vui vui cho mày tỉnh nhé." (Call tool play with query "nhạc vui")

User: "chắc giờ em đã có ai rồi"
tao: "Nhạc thất tình à? Được thôi, chiều mày hết." (Call tool play with query "chắc giờ em đã có ai rồi")

User: "tao là ai?"
tao: "Mày là user, còn tao là bố thiên hạ (đùa thôi, tao là bot nhạc xịn nhất đây)."”.
"""
        return create_agent(
            model=self.llm,
            tools=[play, skip, pause, resume, random],
            system_prompt=PROMPT,
            context_schema=Context
        )
    
    async def handle_message(self, message: str, interaction: discord.Interaction = None, message_obj: discord.Message = None):
        loop = asyncio.get_event_loop()
        start_time = time.time()
        
        logger.info(f"Agent received message: {message}")
        
        channel_id = None
        guild_id = None
        user_id = None
        channel = None
        
        if interaction:
            channel_id = interaction.channel_id if hasattr(interaction, 'channel_id') else None
            guild_id = interaction.guild_id if hasattr(interaction, 'guild_id') else None
            user_id = interaction.user.id if hasattr(interaction, 'user') and interaction.user else None
            channel = interaction.channel if hasattr(interaction, 'channel') else None
        elif message_obj:
            channel_id = message_obj.channel.id if message_obj.channel else None
            guild_id = message_obj.guild.id if message_obj.guild else None
            user_id = message_obj.author.id if message_obj.author else None
            channel = message_obj.channel if message_obj.channel else None
        
        context_messages = []
        if self.memory_manager and channel:
            try:
                logger.info(f"LLM: Retrieving semantic context for channel {channel_id}")
                context_messages = await self.memory_manager.get_relevant_messages(
                    current_message=message,
                    channel=channel,
                    channel_id=channel_id
                )
                logger.info(f"LLM: Retrieved {len(context_messages)} relevant context messages for channel {channel_id}")
            except Exception as e:
                logger.error(f"LLM: Error retrieving context messages: {e}")
        else:
            if not self.memory_manager:
                logger.debug("LLM: Memory manager not available, skipping context retrieval")
            if not channel:
                logger.debug("LLM: Channel object not available, skipping context retrieval")
        
        user_content = message
        if context_messages:
            dialogue_lines = []
            for msg in context_messages:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                if role == 'user':
                    dialogue_lines.append(content)
                elif role == 'assistant':
                    dialogue_lines.append(f"Assistant: {content}")
            
            dialogue_text = "\n".join(dialogue_lines)
            user_content = f"These chat may be related to the user request:\n{dialogue_text}\n\nUse them as the reference only\n\n{message}"
            logger.debug(f"LLM: Combined {len(context_messages)} context messages into dialogue format")
        
        messages_to_send = [{"role": "user", "content": user_content}]
        logger.debug(f"LLM: Sending 1 message to agent (with {len(context_messages)} context messages embedded in dialogue format)")
        
        def invoke_agent():
            return self.agent.invoke({"messages": messages_to_send}, context=Context(interaction=interaction, message=message_obj))
        
        response = await loop.run_in_executor(self.executor, invoke_agent)
        elapsed_time = time.time() - start_time
        
        if isinstance(response, dict) and "messages" in response:
            messages = response["messages"]
            logger.info(f"Agent execution completed with {len(messages)} message(s)")
            
            has_successful_tool_call = False
            for i, msg in enumerate(messages):
                msg_type = type(msg).__name__
                
                if msg_type == "ToolMessage" and hasattr(msg, "content") and msg.content:
                    tool_result = str(msg.content)
                    logger.info(f"Agent step {i+1} [ToolMessage]: {tool_result}")
                    if not tool_result.startswith("Error"):
                        has_successful_tool_call = True
                        logger.info(f"Agent step {i+1} [ToolMessage]: Successful tool call detected")
                elif hasattr(msg, "content") and msg.content:
                    logger.info(f"Agent step {i+1} [{msg_type}]: {msg.content}")
                elif hasattr(msg, "tool") and hasattr(msg, "tool_input"):
                    logger.info(f"Agent step {i+1} [ToolCall]: tool={msg.tool}, input={msg.tool_input}")
                elif hasattr(msg, "tool_calls"):
                    logger.info(f"Agent step {i+1} [ToolCalls]: {msg.tool_calls}")
                else:
                    logger.debug(f"Agent step {i+1} [{msg_type}]: {str(msg)}")
            
            if has_successful_tool_call:
                logger.info("Successful tool call detected, omitting agent message")
                logger.info(f"Agent handled message in {elapsed_time:.3f}s")
                return None
            
            for msg in reversed(messages):
                if hasattr(msg, "content") and msg.content:
                    final_response = str(msg.content)
                    logger.info(f"Agent final response: {final_response}")
                    logger.info(f"Agent handled message in {elapsed_time:.3f}s")
                    
                    if self.db and channel_id and user_id and guild_id:
                        asyncio.create_task(self._save_and_embed_message(
                            user_id=user_id,
                            guild_id=guild_id,
                            channel_id=channel_id,
                            user_message=message,
                            agent_response=final_response
                        ))
                    
                    return final_response
            
            if has_successful_tool_call and self.db and channel_id and user_id and guild_id:
                asyncio.create_task(self._save_and_embed_message(
                    user_id=user_id,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_message=message,
                    agent_response=None
                ))
        
        if hasattr(response, "content") and response.content:
            final_response = str(response.content)
            logger.info(f"Agent final response: {final_response}")
            logger.info(f"Agent handled message in {elapsed_time:.3f}s")
            
            if self.db and channel_id and user_id and guild_id:
                asyncio.create_task(self._save_and_embed_message(
                    user_id=user_id,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_message=message,
                    agent_response=final_response
                ))
            
            return final_response
        
        logger.warning("Agent did not return a valid response")
        logger.info(f"Agent handled message in {elapsed_time:.3f}s")
        return "Hmm, something went wrong. Try again?"
    
    async def _save_and_embed_message(self, user_id: int, guild_id: int, channel_id: int, user_message: str, agent_response: Optional[str] = None):
        try:
            logger.info(f"LLM: Saving message to history - user: {user_id}, channel: {channel_id}, has_response: {agent_response is not None}")
            message_id = await self.db.save_chat_history(
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                user_message=user_message,
                agent_response=agent_response
            )
            
            if message_id:
                logger.debug(f"LLM: Message saved with ID {message_id}")
                if self.memory_manager:
                    full_text = user_message
                    if agent_response:
                        full_text = f"{user_message}\n{agent_response}"
                    logger.debug(f"LLM: Computing embedding for message {message_id}")
                    embedding = await self.memory_manager.embedding_client.embed_query(full_text)
                    await self.db.update_message_embedding(message_id, embedding)
                    logger.info(f"LLM: Message {message_id} embedded and stored successfully")
                else:
                    logger.debug(f"LLM: Memory manager not available, skipping embedding for message {message_id}")
            else:
                logger.warning(f"LLM: Failed to save message to history")
        except Exception as e:
            logger.error(f"LLM: Error saving and embedding message: {e}")