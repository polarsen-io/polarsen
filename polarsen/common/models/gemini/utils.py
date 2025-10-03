__all__ = ("is_gemini_model", "is_thinking_only_model")


def is_gemini_model(model_name: str) -> bool:
    return model_name.startswith("gemini")


_THINKING_ONLY_MODELS = {
    "gemini-2.5-pro",
}


def is_thinking_only_model(model_name: str) -> bool:
    return model_name in _THINKING_ONLY_MODELS
