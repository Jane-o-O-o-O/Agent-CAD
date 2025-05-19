from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from app.infrastructure.config import get_settings
import logging

# 设置模块级别的日志记录器
logger = logging.getLogger(__name__)

class OpenAILLM:
    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base
        )
        
        self.model_name = settings.model_name
        self.temperature = settings.temperature
        self.max_tokens = settings.max_tokens
        logger.info(f"Initialized OpenAI LLM with model: {self.model_name}")
    
    async def ask(self, messages: List[Dict[str, str]], 
                            tools: Optional[List[Dict[str, Any]]] = None,
                            response_format: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send chat request to OpenAI API"""
        response = None
        try:
            if tools:
                logger.debug(f"Sending request to OpenAI with tools, model: {self.model_name}")
                response = await self.client.chat.completions.create(
                    model=self.model_name,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    tools=tools,
                    response_format=response_format,
                )
            else:
                logger.debug(f"Sending request to OpenAI without tools, model: {self.model_name}")
                response = await self.client.chat.completions.create(
                    model=self.model_name,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    response_format=response_format
                )
            return response.choices[0].message.model_dump()
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {str(e)}")
            raise