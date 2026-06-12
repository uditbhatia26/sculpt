"""
llm_factory.py
==============
Per-request LangChain LLM factory for the BYOK (Bring-Your-Own-Key) model.

Usage
-----
    from models.llm_factory import build_llm
    llm = build_llm(provider="openai", api_key="sk-...", model="gpt-4o-mini")
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

# Default models used when the caller omits the model field
PROVIDER_DEFAULTS: dict[str, str] = {
    "openai":      "gpt-4o-mini",
    "anthropic":   "claude-3-5-haiku-20241022",
    "google":      "gemini-1.5-flash",
    "groq":        "llama-3.3-70b-versatile",
    "openrouter":  "meta-llama/llama-3.3-70b-instruct:free",
}

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
    resolved_model = model or PROVIDER_DEFAULTS.get(provider)

    if not resolved_model:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported AI provider: '{provider}'. "
                   f"Supported: {', '.join(PROVIDER_DEFAULTS.keys())}",
        )

    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(api_key=api_key, model=resolved_model)

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
