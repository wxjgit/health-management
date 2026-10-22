"""Minimal data projections approved for model-bound workflows."""


def record_projection(event):
    """Never serialize a database row, nutrition metadata or unrelated feedback."""
    data = event.get('data', event)
    keys = (
        'kind',
        'occurred_at',
        'raw_text',
        'meal_slot',
        'value',
        'quality',
        'exercise_type',
        'intensity',
        'cycle_action',
    )
    result = {key: data[key] for key in keys if key in data}
    if 'kind' not in result:
        result['kind'] = event['kind']
    if 'occurred_at' not in result:
        result['occurred_at'] = event['occurred_at']
    items = []
    for item in data.get('items', [])[:40]:
        projected = {
            key: item[key]
            for key in ('name', 'quantity', 'unit', 'size', 'cooking')
            if key in item
        }
        # Only user-supplied nutrition is needed to interpret an edit.
        sources = item.get('nutrition_sources', {})
        for key in ('kcal', 'protein_g', 'carbs_g', 'fat_g'):
            if sources.get(key) == 'user' or (
                not sources and item.get('source') == 'user'
            ):
                projected[key] = item.get(key)
        items.append(projected)
    result['items'] = items
    return result
