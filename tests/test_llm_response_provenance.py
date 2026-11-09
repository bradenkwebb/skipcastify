"""Which model answered is recorded, so a regression can be checked against it.

A span set that changes with no code change is either sampling or a different
model serving the request, and the two are told apart by the resolved snapshot
and the system fingerprint.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from skipcastify.services import llm_utils


def _openai_reply(**kw):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=0))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))],
        usage=usage, **kw)


def _call_openai(reply):
    client = MagicMock()
    client.chat.completions.create.return_value = reply
    with patch("skipcastify.services.llm_utils.OpenAI", return_value=client), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        return llm_utils.call_openai_chat("prompt")


def test_openai_records_the_resolved_snapshot_and_fingerprint():
    """The request names an alias; the reply names what actually served it."""
    out = _call_openai(_openai_reply(model="gpt-4o-mini-2024-07-18",
                                     system_fingerprint="fp_991a602540"))
    assert out.model == "gpt-4o-mini-2024-07-18"
    assert out.system_fingerprint == "fp_991a602540"


def test_openai_tolerates_a_reply_without_them():
    out = _call_openai(_openai_reply(model=None, system_fingerprint=None))
    assert (out.model, out.system_fingerprint) == ("", "")


def test_ollama_records_its_model_and_has_no_fingerprint():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"response": "[]", "model": "gemma4:e2b",
                              "prompt_eval_count": 7, "eval_count": 3}
    with patch("skipcastify.services.llm_utils.requests.post", return_value=resp):
        out = llm_utils.call_ollama_generate("prompt", model="gemma4:e2b")
    assert out.model == "gemma4:e2b"
    assert out.system_fingerprint == ""
