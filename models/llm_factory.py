"""
llm_factory.py
==============
Per-request LangChain LLM factory for the BYOK (Bring-Your-Own-Key) model.

Usage
-----
    from models.llm_factory import build_llm
    llm = build_llm(provider="openai", api_key="sk-...", model="gpt-6-luna")
    chain = build_res2yaml_chain(llm)

Supported providers
-------------------
  openai      → langchain_openai.ChatOpenAI
  anthropic   → langchain_anthropic.ChatAnthropic
  google      → langchain_google_genai.ChatGoogleGenerativeAI
  groq        → langchain_groq.ChatGroq
  openrouter  → langchain_openai.ChatOpenAI with custom base_url

Error handling
--------------
  • Unknown provider   → raises ValueError (caller converts to HTTP 400)
  • Auth / quota error → re-raised as ProviderAuthError (caller converts to HTTP 402)
"""

from fastapi import HTTPException

# Default model per provider when the caller leaves the model blank: each provider's
# current fast, low-cost text model (checked against the providers' model pages, Nov 2026).
#   openai     gpt-6-luna            replaces gpt-4o-mini (shut down 14 Jan 2027)
#   anthropic  claude-haiku-5-5      replaces claude-3-5-haiku-20241022 (retired)
#   google     gemini-3.8-flash      replaces gemini-1.5-flash (shut down 29 Sep 2025)
#   groq       openai/gpt-oss-120b   replaces llama-3.3-70b-versatile (left free/dev tiers 16 Aug 2026)
#   openrouter none: its free catalogue changes too often to pin; the user names a model
PROVIDER_DEFAULTS: dict[str, str | None] = {
    "openai":      "gpt-6-luna",
    "anthropic":   "claude-haiku-5-5",
    "google":      "gemini-3.8-flash",
    "groq":        "openai/gpt-oss-120b",
    "openrouter":  None,
}

# What the server's own AI uses (non-BYOK users): the first provider with a key in .env
SERVER_PROVIDER_ORDER = (("openai", "OPENAI_API_KEY"), ("groq", "GROQ_API_KEY"))


def _openai_kwargs(model: str) -> dict:
    """gpt-6 models reason by default, and Chat Completions refuses function tools
    (which with_structured_output uses) unless reasoning is off."""
    return {"reasoning_effort": "none"} if model.startswith("gpt-6") else {}

PROVIDER_LABELS: dict[str, str] = {
    "openai":     "OpenAI",
    "anthropic":  "Anthropic",
    "google":     "Google Gemini",
    "groq":       "Groq",
    "openrouter": "OpenRouter",
}


def build_llm(provider: str, api_key: str, model: str | None = None):
    """
    Build and return a LangChain chat model for the given provider + key.

    Parameters
    ----------
    provider : str
        One of: openai, anthropic, google, groq, openrouter
    api_key : str
        The user's API key for that provider.
    model : str | None
        Optional model name override. Falls back to PROVIDER_DEFAULTS.

    Returns
    -------
    A LangChain BaseChatModel instance (not yet invoked).

    Raises
    ------
    HTTPException(400) if the provider is unknown.
    HTTPException(400) if the required package is not installed.
    """
    provider = provider.strip().lower()
    if provider not in PROVIDER_DEFAULTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported AI provider: '{provider}'. "
                   f"Supported: {', '.join(PROVIDER_DEFAULTS.keys())}",
        )
    resolved_model = (model or "").strip() or PROVIDER_DEFAULTS[provider]
    if not resolved_model:
        raise HTTPException(
            status_code=400,
            detail=f"{PROVIDER_LABELS.get(provider, provider)} needs a model name. "
                   "Copy one from openrouter.ai/models into the Model field.",
        )

    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(api_key=api_key, model=resolved_model, **_openai_kwargs(resolved_model))

        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(api_key=api_key, model=resolved_model)

        elif provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(google_api_key=api_key, model=resolved_model)

        elif provider == "groq":
            from langchain_groq import ChatGroq
            return ChatGroq(api_key=api_key, model=resolved_model)

        elif provider == "openrouter":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                api_key=api_key,
                model=resolved_model,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://github.com/uditbhatia26/sculpt",
                    "X-Title": "ResumeSculpt",
                },
            )

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported AI provider: '{provider}'. "
                       f"Supported: {', '.join(PROVIDER_DEFAULTS.keys())}",
            )

    except HTTPException:
        raise
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Server is missing the package for provider '{provider}': {e}. "
                   "Please open a GitHub issue.",
        )


def build_server_llm():
    """The server's own AI (used for non-BYOK users), or None if no provider key is set."""
    import os
    for provider, env in SERVER_PROVIDER_ORDER:
        key = os.getenv(env)
        if key:
            return build_llm(provider, key, PROVIDER_DEFAULTS[provider])
    return None
