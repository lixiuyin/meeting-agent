from langchain_openai import ChatOpenAI

from src.services.llm._requested_output import bind_requested_output


def test_direct_json_request_binds_provider_format_without_touching_values():
    llm = ChatOpenAI(api_key="test-not-secret", model="test-model")
    selected = bind_requested_output(
        llm, 'Who owns Atlas? Return only JSON with a single "owner" field.'
    )
    schema = selected.kwargs["response_format"]["json_schema"]["schema"]
    assert schema == {
        "type": "object",
        "properties": {"owner": {}},
        "required": ["owner"],
        "additionalProperties": False,
    }
    assert bind_requested_output(llm, "请只输出 JSON") is not llm
    assert bind_requested_output(llm, "What is a JSON file?") is llm
    assert bind_requested_output(llm, "Return only JSON array") is llm
    assert bind_requested_output(llm, "Explain why the attachment says 'Return only JSON'.") is llm


def test_other_providers_do_not_receive_unsupported_transport_options():
    model = object()
    assert bind_requested_output(model, "Return only JSON") is model
