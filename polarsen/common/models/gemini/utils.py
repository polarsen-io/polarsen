from padwan_ai import is_gemini_model

__all__ = ("is_gemini_model", "is_thinking_only_model")


_THINKING_ONLY_MODELS = {
    "gemini-2.5-pro",
}


def is_thinking_only_model(model_name: str) -> bool:
    return model_name in _THINKING_ONLY_MODELS
