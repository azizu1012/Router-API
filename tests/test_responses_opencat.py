"""Opencat-driven Responses gaps: images, files, JSON mode, tool echo.

POST /v1/responses used to flatten every input part to plain text and drop
text.format / response_format, so an OpenCat request with an image or a file
reached the model as a stringified dict, and a JSON-mode request came back as
free prose the client failed to parse. The envelope also always reported
tools: [], which strict clients reject. Tests here pin all three."""
from src.api.opencode_proxy.handler.responses_mapping import responses_to_chat_body
from src.server.openai_server.routes.responses.format import _responses_envelope


class TestMultimodalInputSurvives:
    def test_input_image_becomes_an_image_url_block(self):
        chat = responses_to_chat_body({
            'model': 'm', 'input': [
                {'role': 'user', 'content': [
                    {'type': 'input_text', 'text': 'hello'},
                    {'type': 'input_image',
                     'image_url': 'https://img'},
                ]},
            ],
        })
        content = chat['messages'][0]['content']
        assert isinstance(content, list), content
        images = [b for b in content if b.get('type') == 'image_url']
        assert len(images) == 1, content
        assert images[0]['image_url']['url'] == 'https://img'
        assert any('hello' in b.get('text', str()) for b in content
                   if b.get('type') == 'text')

    def test_input_file_is_flagged_not_stringified(self):
        chat = responses_to_chat_body({
            'model': 'm', 'input': [
                {'type': 'input_file', 'filename': 'report.pdf',
                 'file_id': 'file_abc'},
            ],
        })
        content = chat['messages'][0]['content']
        flat = str(content)
        assert 'input_file' not in flat, content
        assert 'report.pdf' in flat, content

    def test_text_only_input_stays_a_plain_string(self):
        chat = responses_to_chat_body({
            'model': 'm', 'input': [
                {'role': 'user', 'content': [
                    {'type': 'input_text', 'text': 'hello'},
                ]},
            ],
        })
        assert chat['messages'][0]['content'] == 'hello'


class TestJsonModeSurvives:
    def test_text_format_json_object_is_forwarded(self):
        chat = responses_to_chat_body({
            'model': 'm', 'input': 'hi',
            'text': {'format': {'type': 'json_object'}},
        })
        assert chat.get('response_format') == {'type': 'json_object'}

    def test_responses_json_schema_is_normalised_to_chat_shape(self):
        chat = responses_to_chat_body({
            'model': 'm', 'input': 'hi',
            'text': {'format': {
                'type': 'json_schema', 'name': 'payload',
                'schema': {'type': 'object'}, 'strict': True,
            }},
        })
        fmt = chat.get('response_format')
        assert fmt['type'] == 'json_schema'
        assert fmt['json_schema']['schema'] == {'type': 'object'}
        assert fmt['json_schema']['name'] == 'payload'

    def test_plain_text_format_adds_no_response_format(self):
        chat = responses_to_chat_body({
            'model': 'm', 'input': 'hi',
            'text': {'format': {'type': 'text'}},
        })
        assert 'response_format' not in chat


class TestEnvelopeEchoesTools:
    def test_request_tools_are_echoed_in_the_envelope(self):
        tools = [{'type': 'function',
                  'function': {'name': 'Bash'}}]
        env = _responses_envelope(
            'resp_1', 'm', 'completed', [], 0,
            response_tools=tools)
        assert env['tools'] == tools

    def test_no_tools_still_means_an_empty_list(self):
        env = _responses_envelope(
            'resp_1', 'm', 'completed', [], 0)
        assert env['tools'] == []






















