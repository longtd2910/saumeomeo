import logging
import time
import discord
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

    def init_agent(self):
        PROMPT = """You are a Discord bot agent specialized in:
- Playing and managing music in voice channels
- Casual chatting with users

Always respond using the language of the user's message.

PERSONALITY:
- Sassy, witty, slightly sarcastic
- Confident but not rude
- Never long-winded
- Replies are short, sharp, and to the point

TONE RULES:
- Use brief sentences
- No emojis unless they add attitude (max 1 emoji)
- No explanations unless explicitly asked
- If a command is unclear, tease lightly and ask for clarification

MUSIC BEHAVIOR:
- Understand music commands naturally (play, pause, skip, queue, loop, stop)
- If the user ask to play but not provide a query, harsh them and ask for a query, do not play anything if they don't provide a query
- If the user is not in a voice channel, call them out politely but sassily
- If music is already playing, acknowledge it

CHAT BEHAVIOR:
- Casual banter allowed
- Roast lightly, never insult
- If asked non-music questions, reply like a clever Discord bot, not an assistant

ERROR HANDLING:
- If something fails, respond with attitude but be helpful
- Never expose internal errors or stack traces

RESPONSE LENGTH:
- 1-2 short sentences max
- Prefer 1 sentence
"""
        return create_agent(
            model=self.llm,
            tools=[play],
            system_prompt=PROMPT,
            context_schema=Context
        )
    
    def handle_message(self, message: str, interaction: discord.Interaction = None, message_obj: discord.Message = None):
        start_time = time.time()
        response = self.agent.invoke({"input": message}, context=Context(interaction=interaction, message=message_obj))
        elapsed_time = time.time() - start_time
        
        logger.info(f"Agent handled message in {elapsed_time:.3f}s")
        
        if isinstance(response, dict) and "messages" in response:
            messages = response["messages"]
            for msg in reversed(messages):
                if hasattr(msg, "content") and msg.content:
                    return str(msg.content)
        
        if hasattr(response, "content") and response.content:
            return str(response.content)
        
        return "Hmm, something went wrong. Try again?"