"""Keep existing extraction scenarios while mocking the new wire protocol."""
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
