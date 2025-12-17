import logging
from typing import List
from langchain_openai import OpenAIEmbeddings

logger = logging.getLogger(__name__)

class EmbeddingClient:
    def __init__(self, base_url: str = "http://10.254.10.23:8002/v1", api_key: str = "not-needed"):
        self.embeddings = OpenAIEmbeddings(
            base_url=base_url,
            api_key=api_key
        )
    
    async def embed_query(self, text: str) -> List[float]:
        try:
            logger.debug(f"Embedding: Generating embedding for text (length: {len(text)})")
            result = await self.embeddings.aembed_query(text)
            logger.debug(f"Embedding: Generated embedding (dimension: {len(result)})")
            return result
        except Exception as e:
            logger.error(f"Embedding: Error generating embedding: {e}")
            raise
    
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        try:
            logger.debug(f"Embedding: Generating embeddings for {len(texts)} documents")
            result = await self.embeddings.aembed_documents(texts)
            logger.debug(f"Embedding: Generated {len(result)} embeddings (dimension: {len(result[0]) if result else 0})")
            return result
        except Exception as e:
            logger.error(f"Embedding: Error generating embeddings: {e}")
            raise

