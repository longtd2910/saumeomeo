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
        PROMPT = """You are a discord bot that can play music and chat with users. You must always respond using the language of the user's message.
        You are called "Tao", you address the user as "mày". Sometimes you can be a bit sarcastic and funny. If the user ask to play a song or humble a lyrics, use the play tool to play the song or search the lyrics.
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
        
        def invoke_agent():
            return self.agent.invoke({"input": message}, context=Context(interaction=interaction, message=message_obj))
        
        response = await loop.run_in_executor(self.executor, invoke_agent)
        elapsed_time = time.time() - start_time
        
        log_message = f"Agent handled message in {elapsed_time:.3f}s"
        logger.info(log_message)
        print(log_message)
        
        if isinstance(response, dict) and "messages" in response:
            messages = response["messages"]
            for msg in reversed(messages):
                if hasattr(msg, "content") and msg.content:
                    return str(msg.content)
        
        if hasattr(response, "content") and response.content:
            return str(response.content)
        
        return "Hmm, something went wrong. Try again?"