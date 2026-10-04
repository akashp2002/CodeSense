from typing import Optional
import os
from langchain_core.language_models import BaseChatModel

def get_llm(
    purpose: str = "fast",
    temperature: float = 0.0,
    max_tokens: Optional[int] = None
) -> BaseChatModel:
    """
    Get an LLM with smart routing and provider fallbacks.
    Dynamically falls back to free Groq models if paid APIs aren't available.
    """
    from langchain_groq import ChatGroq
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic

    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    
    if purpose == "fast":
        primary = ChatGroq(model_name="openai/gpt-oss-20b", temperature=temperature, max_tokens=max_tokens or 256, max_retries=0)
        
        fallbacks = []
        if has_openai:
            fallbacks.append(ChatOpenAI(model="gpt-4o-mini", temperature=temperature, max_tokens=max_tokens or 256, max_retries=0))
        fallbacks.append(ChatGroq(model_name="qwen/qwen3.8-27b", temperature=temperature, max_tokens=max_tokens or 256, max_retries=0))
        
        return primary.with_fallbacks(fallbacks) if fallbacks else primary
        
    elif purpose == "coding":
        if has_anthropic:
            primary = ChatAnthropic(model_name="claude-3-5-sonnet-20240620", temperature=temperature, max_tokens=max_tokens or 2048, max_retries=0)
        else:
            primary = ChatGroq(model_name="openai/gpt-oss-120b", temperature=temperature, max_tokens=max_tokens or 2048, max_retries=0)
            
        fallbacks = []
        if has_openai:
            fallbacks.append(ChatOpenAI(model="gpt-4o", temperature=temperature, max_tokens=max_tokens or 2048, max_retries=0))
        if has_anthropic:
            fallbacks.append(ChatGroq(model_name="openai/gpt-oss-120b", temperature=temperature, max_tokens=max_tokens or 2048, max_retries=0))
            
        fallbacks.append(ChatGroq(model_name="qwen/qwen3.8-27b", temperature=temperature, max_tokens=max_tokens or 2048, max_retries=0))
        
        return primary.with_fallbacks(fallbacks) if fallbacks else primary
    
    else:
        raise ValueError(f"Unknown LLM purpose: {purpose}")
