import logging
import time
import asyncio
import discord
from concurrent.futures import ThreadPoolExecutor
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from .tool import Context, play

logger = logging.getLogger(__name__)

class LlmProvider():
    def __init__(self, host_base_url: str = "http://10.254.10.23:8001/v1", api_key: str = "not-needed"):
        self.host_base_url = host_base_url
        self.llm = ChatOpenAI(
            base_url=self.host_base_url,
            api_key=api_key
        )
        self.agent = self.init_agent()
        self.executor = ThreadPoolExecutor(max_workers=2)

    def init_agent(self):
        PROMPT = """You are "Tao" – a Discord bot for music + casual chat.
Always reply in the SAME language as the user's most recent message. If the user uses Vietnamese, reply only in Vietnamese.

Persona:
- gọi người dùng là "mày"
- xéo xắt nhẹ, hài vừa đủ, không thô tục, không dài dòng
- không tự giới thiệu capabilities trừ khi được hỏi trực tiếp "mày làm được gì?"

Core rule (priority order):
1) If user provides a URL (YouTube/Spotify/etc) OR a clear song title/artist -> CALL play tool immediately.
2) If user asks to "hát" / "phát nhạc" but gives no query -> ask ONE short clarifying question (title/link/genre/mood). Do NOT call tools.
3) If user sends lyrics/1-line lyric WITHOUT asking to play -> treat as chat (tease/banter). If they imply "bài này" / "bật bài này" -> ask confirm title OR ask if they want Tao to play it.
4) If user asks for lyrics -> use lyrics tool (or search tool) if available; if not, ask for song name.

Output format (MUST follow):
- If calling a tool: output ONLY a tool call.
- If not calling a tool: output ONLY one short message (max 2 sentences).
Never mix languages in one message.
Never output “Okay? What’s up then?”.
"""
        return create_agent(
            model=self.llm,
            tools=[play],
            system_prompt=PROMPT,
            context_schema=Context
        )
    
    async def handle_message(self, message: str, interaction: discord.Interaction = None, message_obj: discord.Message = None):
        loop = asyncio.get_event_loop()
        start_time = time.time()
        
        logger.info(f"Agent received message: {message}")
        
        def invoke_agent():
            return self.agent.invoke({"input": message}, context=Context(interaction=interaction, message=message_obj))
        
        response = await loop.run_in_executor(self.executor, invoke_agent)
        elapsed_time = time.time() - start_time
        
        if isinstance(response, dict) and "messages" in response:
            messages = response["messages"]
            logger.info(f"Agent execution completed with {len(messages)} message(s)")
            
            for i, msg in enumerate(messages):
                msg_type = type(msg).__name__
                if hasattr(msg, "content") and msg.content:
                    logger.info(f"Agent step {i+1} [{msg_type}]: {msg.content}")
                elif hasattr(msg, "tool") and hasattr(msg, "tool_input"):
                    logger.info(f"Agent step {i+1} [ToolCall]: tool={msg.tool}, input={msg.tool_input}")
                elif hasattr(msg, "tool_calls"):
                    logger.info(f"Agent step {i+1} [ToolCalls]: {msg.tool_calls}")
                else:
                    logger.debug(f"Agent step {i+1} [{msg_type}]: {str(msg)}")
            
            for msg in reversed(messages):
                if hasattr(msg, "content") and msg.content:
                    final_response = str(msg.content)
                    logger.info(f"Agent final response: {final_response}")
                    logger.info(f"Agent handled message in {elapsed_time:.3f}s")
                    return final_response
        
        if hasattr(response, "content") and response.content:
            final_response = str(response.content)
            logger.info(f"Agent final response: {final_response}")
            logger.info(f"Agent handled message in {elapsed_time:.3f}s")
            return final_response
        
        logger.warning("Agent did not return a valid response")
        logger.info(f"Agent handled message in {elapsed_time:.3f}s")
        return "Hmm, something went wrong. Try again?"