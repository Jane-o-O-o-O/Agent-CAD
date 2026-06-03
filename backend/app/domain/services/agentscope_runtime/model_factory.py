from agentscope.agent import ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel

from app.core.config import Settings


def create_agentscope_model(settings: Settings) -> OpenAIChatModel:
    """Build an AgentScope OpenAI-compatible chat model from app settings."""
    return OpenAIChatModel(
        credential=OpenAICredential(
            api_key=settings.api_key or "",
            base_url=settings.api_base,
        ),
        model=settings.model_name,
        parameters=OpenAIChatModel.Parameters(
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            reasoning_effort=settings.model_reasoning_effort,
            thinking_enable=bool(settings.model_reasoning_effort),
        ),
        stream=True,
        max_retries=settings.agentscope_max_retries,
        context_size=settings.agentscope_context_size,
    )


def create_agentscope_react_config(settings: Settings) -> ReActConfig:
    return ReActConfig(max_iters=settings.agentscope_max_iters)

