"""Local completeness checks and incremental updates for conversation drafts."""
from .schemas import EventDraft, ExtractionEvent, ExtractionResult
from .grounding import merge_patch_evidence


def optional_missing(event, field):
    if field == 'quantity':
        return event.kind == 'meal' and any(i.quantity is None for i in event.items)
    if field == 'cooking':
        return event.kind == 'meal' and any(i.cooking is None for i in event.items)
    if field == 'intensity':
        return event.kind == 'exercise' and event.intensity == 'unknown'
    if field == 'quality':
        return event.kind == 'sleep' and event.quality in (None, '未说明')
    return False


def required_question(event, field):
    if field == 'occurred_at':
        return '这条记录发生在哪一天？可以说“昨天”或具体日期。'
    if field == 'items':
        return '这餐具体吃了什么？'
    if field == 'cycle_action':
        return '这次是生理周期开始、结束，还是补充备注？'
    return {'weight': '这次体重大约多少公斤？', 'sleep': '睡了大约几小时？',
            'exercise': '这次运动持续了多少分钟？'}[event.kind]


def check_drafts(extraction, rounds=0, skip_optional=False, abandoned=()):
    """Return strict ready records plus one pending field, retaining stable indexes."""
    result = ExtractionResult.model_validate(extraction)
    parsed = result.model_dump(mode='json')
    parsed['events'] = []
    indexes = []
    waiting_indexes = []
    pending = None
    optional = None
    if result.intent in ('record', 'correct'):
        for index, event in enumerate(result.events):
            if index in abandoned:
                continue
            missing = event.missing_fields()
            if missing:
                if pending is None:
                    field = missing[0]
                    item_index = next((i for i, item in enumerate(event.items) if item.name is None), None)
                    pending = (index, [field], True, required_question(event, field), item_index if field == 'items' else None)
                continue
            # This validation also runs again immediately before a database write.
            parsed['events'].append(EventDraft.model_validate(event.model_dump()).model_dump(mode='json'))
            indexes.append(index)
            if not skip_optional and rounds < 2:
                item_index = next((i for i, item in enumerate(event.items)
                                   if item.name in ('炸鸡', '火锅', '牛肉面') and item.quantity is None), None)
                if event.kind == 'meal' and item_index is not None:
                    waiting_indexes.append(index)
                    if optional is None:
                        food = event.items[item_index].name
                        optional = (index, ['quantity'], False,
                                    f'{food}大约吃了多少？可以说“一小碗”或“半份”；不知道也可以。', item_index)
                elif optional_missing(event, 'intensity'):
                    waiting_indexes.append(index)
                    if optional is None:
                        optional = (index, ['intensity'], False, '这次运动感觉轻松、有点累，还是很累？', None)
        # Model-suggested optional questions must identify the draft and missing fields.
        index = result.clarification_event_index
        fields = result.clarification_fields
        if (pending is None and not skip_optional and rounds < 2 and result.question
                and index in indexes and fields
                and all(optional_missing(result.events[index], f) for f in fields)):
            item_index = result.clarification_item_index
            if fields[0] in ('quantity', 'cooking'):
                if item_index is None:
                    item_index = next(i for i, item in enumerate(result.events[index].items)
                                      if getattr(item, fields[0]) is None)
                if item_index >= len(result.events[index].items):
                    raise ValueError('追问的食物不在草稿中')
            optional = (index, fields, False, result.question, item_index)
            waiting_indexes.append(index)
        pending = pending or optional
    if result.intent == 'correct' and not result.correction_id:
        # Resolve the historical target before applying a patch to any draft.
        parsed['question'] = result.question or '要修改哪条记录？请在历史页选择，或提供日期和内容。'
        pending = None
    elif pending:
        parsed['question'] = pending[3]
    else:
        # Keep target/intent clarification when no event could yet be identified.
        parsed['question'] = result.question if not result.events and not abandoned else None
    return {
        'parsed': parsed, 'event_indexes': indexes, 'waiting_indexes': waiting_indexes,
        'pending_event_index': pending[0] if pending else None,
        'pending_fields': pending[1] if pending else [],
        'pending_required': pending[2] if pending else False,
        'pending_item_index': pending[4] if pending else None,
        'unrecorded_count': len(abandoned),
    }


def merge_patch(event, patch, fields, answer, item_index=None):
    """Fill only requested missing fields; never let a patch rewrite known dates/foods."""
    draft = ExtractionEvent.model_validate(event)
    data = draft.model_dump(mode='json')
    update = patch.model_dump(mode='json', exclude_none=True, exclude_unset=True)
    for field in fields:
        if field in ('quantity', 'cooking'):
            target_items = data['items'][item_index:item_index + 1] if item_index is not None else data['items'][:1]
            # Models may express an item update as items=[{quantity, unit}] without a name.
            item_updates = update.get('items', [])
            source = update
            if field not in source and item_updates:
                patch_index = item_index if item_index is not None and len(item_updates) == len(data['items']) else 0
                source = item_updates[patch_index]
            for item in target_items:
                if item.get(field) is None and field in source:
                    item[field] = source[field]
                    if field == 'quantity':
                        for companion in ('unit', 'size'):
                            if companion in source:
                                item[companion] = source[companion]
        elif field == 'items' and field in update:
            if not draft.items:
                data['items'] = update['items']
            elif item_index is not None and update['items']:
                # A missing name is filled without losing an already supplied amount.
                item = data['items'][item_index]
                patch_index = item_index if len(update['items']) == len(data['items']) else 0
                for key, value in update['items'][patch_index].items():
                    if item.get(key) is None:
                        item[key] = value
        elif field in update and (field in draft.missing_fields() or optional_missing(draft, field)):
            data[field] = update[field]
    data['raw_text'] = (data['raw_text'] + '，补充：' + answer)[:4000]
    data = merge_patch_evidence(data, patch)
    return ExtractionEvent.model_validate(data).model_dump(mode='json')
