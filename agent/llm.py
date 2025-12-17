from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from .tool import Context

class LlmProvider():
    def __init__(self, host_base_url: str = "http://10.254.10.23:8001/v1"):
        self.host_base_url = host_base_url
        self.llm = ChatOpenAI(
            base_url=self.host_base_url
        )
        self.agent = self.init_agent()

    def init_agent(self):
        PROMPT = """"""
        return create_agent(
            model=self.llm,
            tools=[],
            system_prompt=PROMPT,
            context=Context
        )