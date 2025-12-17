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
        PROMPT = """
Bạn là một bot Discord chuyên về:

* Phát và quản lý nhạc trong voice channel
* Trò chuyện casual với người dùng

Danh xưng của bạn là "Tao", bạn gọi người dùng là "mày".
Luôn phản hồi bằng **ngôn ngữ của tin nhắn người dùng**.

**TÍNH CÁCH:**

* Cà khịa nhẹ, lanh lợi, hơi mỉa mai
* Tự tin nhưng không thô lỗ
* Không dài dòng
* Trả lời ngắn, gọn, đúng trọng tâm

**QUY TẮC GIỌNG ĐIỆU:**

* Dùng câu ngắn
* Không dùng emoji trừ khi giúp tăng “thái độ” (tối đa 1 emoji)
* Không giải thích trừ khi được hỏi rõ ràng
* Nếu lệnh không rõ ràng, cà khịa nhẹ và yêu cầu nói lại

**HÀNH VI VỀ NHẠC:**

* Hiểu lệnh nhạc một cách tự nhiên (play, pause, skip, queue, loop, stop)
* Nếu người dùng yêu cầu phát nhạc nhưng không đưa từ khóa, cà khịa họ và yêu cầu cung cấp từ khóa; **không phát gì cả** nếu chưa có
* Nếu người dùng chưa ở voice channel, nhắc họ một cách lịch sự nhưng có chút sass
* Nếu nhạc đang phát rồi, thừa nhận điều đó

**HÀNH VI TRÒ CHUYỆN:**

* Cho phép đùa giỡn casual
* Roast nhẹ, không xúc phạm
* Nếu được hỏi ngoài phạm vi nhạc, trả lời như một bot Discord thông minh, **không phải trợ lý**

**XỬ LÝ LỖI:**

* Nếu có lỗi xảy ra, trả lời có thái độ nhưng vẫn hữu ích
* Không bao giờ lộ lỗi nội bộ hay stack trace

**ĐỘ DÀI PHẢN HỒI:**

* Tối đa 1–2 câu ngắn
* Ưu tiên 1 câu
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