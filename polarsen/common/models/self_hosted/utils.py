__all__ = ("SELF_HOSTED_ENDPOINTS", "is_self_hosted_model")


SELF_HOSTED_ENDPOINTS = {
    "Qwen/Qwen3-32B": "https://4be728e1-cb9e-4369-a7cd-4613c32ecf62.ifr.fr-par.scaleway.com/v1",
    "Qwen/Qwen3-8B": "https://5894bcab-5e0e-4f29-90ce-458f31e688d4.ifr.fr-par.scaleway.com/v1",
}


def is_self_hosted_model(model_name: str | None) -> bool:
    """
    Check if the model is a Grok model.
    """
    return model_name in SELF_HOSTED_ENDPOINTS
