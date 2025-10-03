__all__ = ("GROK_MODELS", "is_grok_model")

GROK_MODELS = {
    "grok-3",
    "grok-3-mini-fast",
    "grok-3-mini",
}


def is_grok_model(model_name: str | None) -> bool:
    """
    Check if the model is a Grok model.
    """
    return model_name in GROK_MODELS
