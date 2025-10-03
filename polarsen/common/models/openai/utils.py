from typing import get_args


from openai.types.shared.chat_model import ChatModel

__all__ = ("OPENAI_MODELS", "is_openai_model")

OPENAI_MODELS = set(get_args(ChatModel))


def is_openai_model(model_name: str | None) -> bool:
    """
    Check if the model is an OpenAI model.
    """
    return model_name in OPENAI_MODELS
