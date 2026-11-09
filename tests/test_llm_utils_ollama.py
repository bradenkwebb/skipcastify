"""The Ollama route is the default provider, so its request has to be right."""

from unittest.mock import MagicMock, patch

from skipcastify.services import llm_utils


def _captured_payload(reply=None):
    resp = MagicMock(status_code=200)
    resp.json.return_value = reply or {"response": "[]", "prompt_eval_count": 7,
                                       "eval_count": 3}
    with patch("skipcastify.services.llm_utils.requests.post",
               return_value=resp) as post:
        out = llm_utils.call_ollama_generate("a prompt", model="m")
    return post.call_args.kwargs["json"], out


def test_temperature_is_under_options_where_ollama_reads_it():
    """At the top level Ollama drops it and generates at its default (0.8)."""
    payload, _ = _captured_payload()
    assert payload["options"]["temperature"] == 0.0
    assert "temperature" not in payload, \
        "a top-level temperature is ignored by /api/generate"


def test_the_rest_of_the_request_is_unchanged():
    payload, _ = _captured_payload()
    assert payload["model"] == "m"
    assert payload["prompt"] == "a prompt"
    assert payload["stream"] is False


def test_token_counts_come_back():
    _, out = _captured_payload()
    assert (out.text, out.prompt_tokens, out.completion_tokens) == ("[]", 7, 3)
    assert out.cost_usd == 0.0


def test_llm_provider_defaults_to_ollama():
    """Not a fallback: with LLM_PROVIDER unset this is the route taken."""
    with patch.dict("os.environ", {}, clear=True), \
         patch("skipcastify.services.llm_utils.call_ollama_generate") as ollama:
        llm_utils.call_llm("p")
    assert ollama.called
