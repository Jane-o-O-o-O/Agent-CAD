from agentscope.agent import ReActConfig
from agentscope.credential import DeepSeekCredential
from agentscope.credential import OpenAICredential
from agentscope.model import DeepSeekChatModel
from agentscope.model import OpenAIChatModel

from app.core.config import Settings


def create_agentscope_model(settings: Settings) -> OpenAIChatModel | DeepSeekChatModel:
    """Build an AgentScope OpenAI-compatible chat model from app settings."""
    if _is_deepseek_model(settings):
        return _create_deepseek_model(settings)

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


def _create_deepseek_model(settings: Settings) -> DeepSeekChatModel:
    thinking_type = (settings.deepseek_thinking_type or "disabled").lower()
    parameters = {
        "max_tokens": settings.max_tokens,
        "thinking_enable": thinking_type == "enabled",
    }
    if thinking_type != "enabled":
        parameters["temperature"] = settings.temperature
    if settings.model_reasoning_effort and thinking_type == "enabled":
        parameters["reasoning_effort"] = _normalize_deepseek_reasoning_effort(
            settings.model_reasoning_effort,
        )

    return DeepSeekChatModel(
        credential=DeepSeekCredential(
            api_key=settings.api_key or "",
            base_url=settings.api_base or "https://api.deepseek.com",
        ),
        model=settings.model_name,
        parameters=DeepSeekChatModel.Parameters(**parameters),
        stream=True,
        max_retries=settings.agentscope_max_retries,
        context_size=settings.agentscope_context_size,
    )


def _is_deepseek_model(settings: Settings) -> bool:
    model_name = settings.model_name.lower()
    api_base = (settings.api_base or "").lower()
    return "deepseek" in model_name or "api.deepseek.com" in api_base


def _normalize_deepseek_reasoning_effort(value: str) -> str:
    normalized = value.lower()
    if normalized in ("max", "xhigh"):
        return "max"
    return "high"


def _is_reasoning_model(model_name: str) -> bool:
    normalized = model_name.lower()
    return any(marker in normalized for marker in ("deepseek-r1", "/r1", "-r1", "reasoning"))

