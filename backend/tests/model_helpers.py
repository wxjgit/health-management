"""Keep existing extraction scenarios while mocking the new wire protocol."""
import json
import httpx


def safety_transport(handler):
    """Isolate legacy business tests from classifier semantics.

    Safety integration tests use their own transport and exercise the real
    classification contract, failures and malicious outputs separately.
    """
    def wrapped(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        if data.get('task') == 'safety_classification':
            return httpx.Response(200, json={'choices': [{'message': {
                'content': json.dumps({'findings': [], 'uncertain': False}),
            }}]})
        return handler(request)
    return wrapped


def planned_content(data, content):
    if not isinstance(content, dict):
        return content
    if data.get('task') == 'processing_plan' and 'intent' in content:
        action = {k: v for k, v in content.items() if k != 'correction_id'}
        if action['intent'] == 'correct':
            action['events'] = []
        return {'actions': [{'text': data['text'], **action}]}
    if data.get('task') == 'correct_selected' and 'intent' in content:
        return {'event': next(iter(content.get('events', [])), None), 'question': content.get('question')}
    return content
