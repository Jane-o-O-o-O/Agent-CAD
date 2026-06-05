from agentscope.agent import ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel

from app.core.config import Settings


def create_agentscope_model(settings: Settings) -> OpenAIChatModel:
    """Build an AgentScope OpenAI-compatible chat model from app settings."""
    parameters = {
        "max_tokens": settings.max_tokens,
        "temperature": settings.temperature,
    }
    if settings.model_reasoning_effort:
        parameters["reasoning_effort"] = settings.model_reasoning_effort
        if _is_reasoning_model(settings.model_name):
            parameters["thinking_enable"] = True

    return OpenAIChatModel(
        credential=OpenAICredential(
            api_key=settings.api_key or "",
            base_url=settings.api_base,
        ),
        model=settings.model_name,
        parameters=OpenAIChatModel.Parameters(**parameters),
        stream=True,
        max_retries=settings.agentscope_max_retries,
        context_size=settings.agentscope_context_size,
    )


def create_agentscope_react_config(settings: Settings) -> ReActConfig:
    return ReActConfig(max_iters=settings.agentscope_max_iters)


def _is_reasoning_model(model_name: str) -> bool:
    normalized = model_name.lower()
    return any(marker in normalized for marker in ("deepseek-r1", "/r1", "-r1", "reasoning"))

