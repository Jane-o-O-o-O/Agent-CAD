import os
import json
import logging
import httpx
from pydantic_settings import BaseSettings
from functools import lru_cache

logger = logging.getLogger(__name__)

_OPENAI_SDK_HEADERS_TO_STRIP = {
    "x-stainless-lang",
    "x-stainless-package-version",
    "x-stainless-os",
    "x-stainless-arch",
    "x-stainless-runtime",
    "x-stainless-runtime-version",
    "x-stainless-async",
    "x-stainless-helper-method",
    "x-stainless-retry-count",
}


async def _strip_openai_sdk_headers(request: httpx.Request) -> None:
    for header in list(request.headers.keys()):
        if header.lower() in _OPENAI_SDK_HEADERS_TO_STRIP:
            del request.headers[header]
    request.headers["User-Agent"] = "python-httpx/0.28.1"


def _parse_extra_headers() -> dict | None:
    raw = os.environ.get("EXTRA_HEADERS")
    if not raw:
        return None
    try:
        headers = json.loads(raw)
        if isinstance(headers, dict):
            return headers
        logger.warning("EXTRA_HEADERS is not a JSON object, ignoring")
    except json.JSONDecodeError:
        logger.warning("EXTRA_HEADERS is not valid JSON, ignoring")
    return None


class Settings(BaseSettings):
    
    # Model provider configuration
    api_key: str | None = None
    api_base: str | None = None
    
    # Model configuration
    model_name: str = "gpt-4o"
    model_provider: str = "openai"
    temperature: float = 0.7
    max_tokens: int = 2000
    agent_framework: str = "langchain"
    agentscope_context_size: int = 128000
    agentscope_max_iters: int = 20
    agentscope_max_retries: int = 3
    model_reasoning_effort: str | None = None
    service_tier: str | None = None
    strip_openai_sdk_headers: bool = False
    browser_model_name: str | None = None
    claw_model_name: str | None = None
    reasoning_model_name: str | None = None
    long_context_model_name: str | None = None
    vision_model_name: str | None = None
    embedding_model_name: str | None = None
    reranker_model_name: str | None = None
    
    # MongoDB configuration
    mongodb_uri: str = "mongodb://mongodb:27017"
    mongodb_database: str = "manus"
    mongodb_username: str | None = None
    mongodb_password: str | None = None
    
    # Redis configuration
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    
    # Sandbox configuration
    sandbox_address: str | None = None
    sandbox_image: str | None = None
    sandbox_name_prefix: str | None = None
    sandbox_ttl_minutes: int | None = 30
    sandbox_network: str | None = None  # Docker network bridge name
    sandbox_chrome_args: str | None = ""
    sandbox_https_proxy: str | None = None
    sandbox_http_proxy: str | None = None
    sandbox_no_proxy: str | None = None

    # Browser engine configuration
    browser_engine: str = "browser_use"  # "playwright" or "browser_use"
    
    # Search engine configuration
    search_provider: str | None = "bing_web"  # "baidu", "baidu_web", "google", "bing", "bing_web", "tavily", "serper", "custom"
    baidu_search_api_key: str | None = None
    bing_search_api_key: str | None = None
    google_search_api_key: str | None = None
    google_search_engine_id: str | None = None
    tavily_api_key: str | None = None
    # Serper.dev search configuration (SEARCH_PROVIDER=serper)
    serper_api_key: str | None = None
    # Custom search API configuration (SEARCH_PROVIDER=custom)
    search_api_url: str | None = None
    search_api_key: str | None = None
    search_api_key_header: str = "Authorization"
    search_api_key_header_prefix: str = "Bearer "
    search_api_key_param: str = ""
    search_api_method: str = "POST"
    search_query_field: str = "q"
    search_result_field: str = "results"
    search_title_field: str = "title"
    search_link_field: str = "link"
    search_snippet_field: str = "snippet"
    
    # Google Analytics configuration
    google_analytics_id: str | None = None

    # Auth configuration
    auth_provider: str = "password"  # "password", "none", "local"
    show_github_button: bool = False
    github_repository_url: str = ""
    password_salt: str | None = None
    password_hash_rounds: int = 10
    password_hash_algorithm: str = "pbkdf2_sha256"
    local_auth_email: str = "admin@example.com"
    local_auth_password: str = "admin"
    
    # Email configuration
    email_host: str | None = None  # "smtp.gmail.com"
    email_port: int | None = None  # 587
    email_username: str | None = None
    email_password: str | None = None
    email_from: str | None = None
    
    # JWT configuration
    jwt_secret_key: str = "your-secret-key-here"  # Should be set in production
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7
    
    # Extra headers for LLM requests (parsed from EXTRA_HEADERS env var, JSON)
    extra_headers: dict | None = None
    
    # Claw (OpenClaw) configuration
    claw_enabled: bool = False
    claw_image: str = "simpleyyt/manus-claw"
    claw_name_prefix: str = "manus-claw"
    claw_ttl_seconds: int = 3600
    claw_network: str | None = None  # Docker network bridge name for claw containers
    claw_ready_timeout: int = 300  # Max seconds to wait for claw container to become ready
    claw_address: str | None = None  # If set, use this fixed host instead of creating Docker containers
    claw_api_key: str | None = None  # Static API key accepted by the LLM proxy (for dev/fixed container)
    manus_api_base_url: str = "http://backend:8000"  # URL of this backend accessible from claw containers

    # MCP configuration
    mcp_config_path: str = "/etc/mcp.json"

    # Codex-style skills configuration
    skills_enabled: bool = True
    skills_paths: str = "/app/skills:~/.codex/skills"
    skills_include_system: bool = True
    skills_max_selected: int = 3
    skills_max_body_chars: int = 20000

    # CAD file intake configuration
    cad_docling_enabled: bool = False
    
    # Logging configuration
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        
    def validate(self):
        """Validate configuration settings"""
        if not self.api_key:
            raise ValueError("API key is required")
        if self.agent_framework not in {"langchain", "agentscope"}:
            raise ValueError("AGENT_FRAMEWORK must be 'langchain' or 'agentscope'")

    def chat_model_kwargs(self, model_name: str | None = None) -> dict:
        """Build shared kwargs for OpenAI-compatible LangChain chat models."""
        kwargs = dict(
            model=model_name or self.model_name,
            model_provider=self.model_provider,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            base_url=self.api_base,
        )
        if self.model_reasoning_effort:
            kwargs["reasoning_effort"] = self.model_reasoning_effort
        if self.service_tier:
            kwargs["service_tier"] = self.service_tier
        if self.extra_headers:
            kwargs["default_headers"] = self.extra_headers
        if self.strip_openai_sdk_headers:
            kwargs["http_async_client"] = httpx.AsyncClient(
                event_hooks={"request": [_strip_openai_sdk_headers]},
                timeout=120.0,
            )
        return kwargs

@lru_cache()
def get_settings() -> Settings:
    """Get application settings"""
    if not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.getenv("API_KEY")
    settings = Settings()
    settings.extra_headers = _parse_extra_headers()
    settings.validate()
    return settings 
