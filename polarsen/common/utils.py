from typing import get_args, Literal, TypeGuard

import niquests

from .models import mistral, gemini, openai, grok, self_hosted

__all__ = ("setup_session_model", "AISource", "get_source_from_model", "is_valid_source")

AISource = Literal["mistral", "gemini", "openai", "grok", "self_hosted"]


def is_valid_source(source: str | None) -> TypeGuard[AISource]:
    if source is None or source not in get_args(AISource):
        return False

    return True


def get_source_from_model(model_name: str | None = None, agent_name: str | None = None) -> AISource:
    """
    Determine the AI source based on the model or agent name.
    Raises ValueError if neither model_name nor agent_name is provided.
    """
    if not model_name and not agent_name:
        raise ValueError("Either model_name or agent_name must be provided")

    _model_name = model_name or ""
    _agent_name = agent_name or ""

    if mistral.is_mistral_model(_model_name) or mistral.is_mistral_agent(_agent_name):
        return "mistral"
    elif gemini.is_gemini_model(_model_name):
        return "gemini"
    elif openai.is_openai_model(_model_name):
        return "openai"
    elif grok.is_grok_model(_model_name):
        return "grok"
    elif self_hosted.is_self_hosted_model(_model_name):
        return "self_hosted"

    raise ValueError(f"Unknown model name {_model_name!r} or agent name {_agent_name!r}")


def setup_session_model(
    session: niquests.Session,
    model_name: str | None = None,
    agent_name: str | None = None,
    api_key: str | None = None,
) -> tuple[AISource, str | None, str | None]:
    """
    Setup the session with appropriate headers based on the model or agent name.
    """
    if not model_name and not agent_name:
        raise ValueError("Either model_name or agent_name must be provided")

    _model_name = model_name or ""
    _agent_name = agent_name or ""

    if mistral.is_mistral_model(_model_name) or mistral.is_mistral_agent(_agent_name):
        _source = "mistral"
        mistral.set_headers(session, api_key=api_key)
    elif gemini.is_gemini_model(_model_name):
        gemini.set_headers(session, api_key=api_key)
        _source = "gemini"
    elif openai.is_openai_model(_model_name):
        openai.set_headers(session, api_key=api_key)
        _source = "openai"
    elif grok.is_grok_model(_model_name):
        grok.set_headers(session, api_key=api_key)
        _source = "grok"
    elif self_hosted.is_self_hosted_model(_model_name):
        _source = "self_hosted"
        self_hosted.set_headers(session)
    else:
        raise ValueError(f"Unknown model name {_model_name!r} or agent name {_agent_name!r}")

    if not is_valid_source(_source):
        raise ValueError(f"Invalid source {_source!r}")

    return _source, model_name, agent_name
