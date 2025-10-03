__all__ = ("is_mistral_model", "MISTRAL_MODELS", "MISTRAL_AGENTS", "is_mistral_agent")

MISTRAL_MODELS = {
    "mistral-large-latest",
    "pixtral-large-latest",
    "mistral-medium-latest",
    "mistral-moderation-latest",
    "ministral-3b-latest",
    "ministral-8b-latest",
    "open-mistral-nemo",
    "mistral-small-latest",
    "devstral-small-latest",
    "mistral-saba-latest",
    "codestral-latest",
    "mistral-ocr-latest",
    "magistral-small-latest",
    "mistral-small-latest"
}

MISTRAL_AGENTS = {"discussion": "ag:ad832830:20250516:untitled-agent:38dc4ce3"}


def is_mistral_model(model_name: str) -> bool:
    """
    Check if the model name is a Mistral model.
    """
    return model_name.startswith("mistral-") or model_name in MISTRAL_MODELS


def is_mistral_agent(agent_name: str) -> bool:
    return agent_name in MISTRAL_AGENTS.values()
