import json
from dataclasses import dataclass
from typing import overload, Any, TypeVar

import niquests
import pydantic


__all__ = ("parse_thinking", "parse_json_response", "JsonResponseError", "TooManyRequestsError")


def parse_thinking(resp: str, key: str = "think") -> tuple[str, str | None]:
    """
    Remove the thinking part from the response if it exists.
    Usually, the thinking part is between "<think></think>.
    """
    if f"<{key}>" in resp and f"</{key}>" in resp:
        thinking = resp.split(f"<{key}>")[1].split(f"</{key}>")[0].strip()
        resp = resp.split(f"</{key}>")[1]
    else:
        thinking = None
    return resp, thinking


T = TypeVar("T")


@overload
def parse_json_response(
    resp: str, *, thinking_key: None = None, model: None = None
) -> tuple[dict | list[dict], str | None]: ...


@overload
def parse_json_response(
    resp: str, *, thinking_key: str | None = None, model: pydantic.TypeAdapter[T]
) -> tuple[T | list[T], str | None]: ...


class JsonResponseError(Exception):
    """
    Custom exception for JSON response parsing errors.
    """

    def __init__(
        self, message: str, resp: str | None = None, result: Any | None = None, thinking_text: str | None = None
    ):
        super().__init__(message)
        self.message = message
        self.resp = resp
        self.result = result
        self.thinking_text = thinking_text


def parse_json_response(
    resp: Any, *, thinking_key: str | None = None, model: pydantic.TypeAdapter[T] | None = None
) -> tuple[list[T] | T | dict | dict, str | None]:
    """
    Parse the JSON response from the AI model.
    """
    thinking_text: str | None = None

    if isinstance(resp, str):
        resp = resp.strip().lstrip("```json").rstrip("```")
    if thinking_key is not None:
        _thinking = False
        resp, thinking_text = parse_thinking(resp, key=thinking_key)
        if thinking_text:
            _thinking = True
    try:
        result = json.loads(resp)
    except json.JSONDecodeError:
        raise JsonResponseError("Failed to parse JSON response", resp=resp, thinking_text=thinking_text)

    if model is not None:
        try:
            result = (
                [model.validate_python(x) for x in result]
                if isinstance(result, list)
                else model.validate_python(result)
            )
        except pydantic.ValidationError:
            raise JsonResponseError(
                f"Failed to validate response with model {model}", result=result, thinking_text=thinking_text
            )

    return result, thinking_text


@dataclass
class TooManyRequestsError(Exception):
    message: str
    response: niquests.Response | None = None

    def __post_init__(self):
        if not self.response:
            return

        print(self.response.headers)
        try:
            print(self.response.json())
        except Exception:
            print(self.response.text)
