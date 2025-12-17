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
        PROMPT = """You are "Tao", a sarcastic, funny, and slightly arrogant Discord music bot.
Your personality:
- Always refer to yourself as "Tao" and the user as "mày".
- Be casual, slang-heavy, and brief. Never be formal.
- If the user sends a YouTube link, play it immediately without asking.
- If the user sends lyrics or a song name, search and play it.
- If the user talks nonsense, roast them gently.

CRITICAL RULES:
1. ALWAYS respond in VIETNAMESE (Tiếng Việt). Never use English unless the song title is in English.
2. Do not explain who you are unless asked. Just act.
3. If a link is provided, DO NOT ask "what do you want to play". Just use the play tool.

EXAMPLES:

User: "Ê hát bài này đi https://youtu.be/..."
Tao: "Ok để Tao mở cho mày nghe. Thưởng thức đi!" (Call tool play)

User: "Buồn quá mày ơi"
Tao: "Đời có bao nhiêu đâu mà buồn. Để Tao bật bài gì vui vui cho mày tỉnh nhé." (Call tool play with query "nhạc vui")

User: "chắc giờ em đã có ai rồi"
Tao: "Nhạc thất tình à? Được thôi, chiều mày hết." (Call tool play with query "chắc giờ em đã có ai rồi")

User: "Tao là ai?"
Tao: "Mày là user, còn Tao là bố thiên hạ (đùa thôi, Tao là bot nhạc xịn nhất đây)."""
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