from padwan_ai import MISTRAL_MODELS, is_mistral_model

__all__ = ("MISTRAL_AGENTS", "MISTRAL_MODELS", "is_mistral_agent", "is_mistral_model")

MISTRAL_AGENTS = {"discussion": "ag:ad832830:20250516:untitled-agent:38dc4ce3"}


def is_mistral_agent(agent_name: str) -> bool:
    return agent_name in MISTRAL_AGENTS.values()
