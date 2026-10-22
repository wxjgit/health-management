"""Bounded offline parser for demo mode."""

import re
from datetime import date, datetime, timedelta

from ..common.clock import date_from_text, now
from ..contracts.events import ExtractionEvent, MealItem
from ..contracts.workflows import ExtractionResult


NUMBERS = {
    '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '半': .5,
}
NUMBER = r'(\d+(?:\.\d+)?|[一二两三四五六七八九十半])'
FOODS = [
    '牛肉面', '米饭', '鸡胸肉', '鸡蛋', '卤蛋', '牛奶', '酸奶', '燕麦',
    '面包', '苹果', '香蕉', '西兰花', '沙拉', '炸鸡', '火锅', '饺子',
    '豆腐', '面条', '红薯', '咖啡', '奶茶',
]
SLOTS = {
    '早': 'breakfast',
    '早餐': 'breakfast',
    '中午': 'lunch',
    '午餐': 'lunch',
    '晚餐': 'dinner',
    '晚饭': 'dinner',
    '加餐': 'snack',
    '夜宵': 'snack',
}


def number(value):
    return NUMBERS[value] if value in NUMBERS else float(value)


def slot_for(text):
    return next((slot for word, slot in SLOTS.items() if word in text), None)


def at_slot(day, slot):
    return day.replace(
        hour={
            'breakfast': 8, 'lunch': 12, 'dinner': 18, 'snack': 21,
        }.get(slot, 12)
    )


def duration(text):
    if '半小时' in text:
        return 30
    match = re.search(NUMBER + r'\s*(小时|分钟|分)', text)
    return number(match[1]) * (60 if match[2] == '小时' else 1) if match else None


def demo_parse(text, recent, selected_id=None, original=None):
    if any(word in text for word in ['先不记', '不记了', '取消记录']):
        return ExtractionResult(intent='advice')
    correction = any(
        word in text for word in ['不是', '改成', '修改', '纠正']
    ) or selected_id is not None
    if correction:
        candidates = recent
        if selected_id:
            candidates = [row for row in recent if row['id'] == selected_id]
        else:
            named = [food for food in FOODS if food in text]
            if named:
                candidates = [
                    row for row in candidates
                    if any(food in row['raw_text'] for food in named)
                ]
            if (
                any(word in text for word in ['昨天', '前天'])
                or re.search(r'20\d{2}-\d{2}-\d{2}', text)
            ):
                day = date_from_text(text).date().isoformat()
                candidates = [row for row in candidates if row['day'] == day]
            elif any(word in text for word in ['刚才', '上一条']) or text.startswith('不是'):
                candidates = candidates[:1]
        if len(candidates) != 1:
            return ExtractionResult(
                intent='correct',
                question='要修改哪条记录？请在历史页点击“纠正”，或提供记录日期和内容。',
            )
        prior = candidates[0]
        changed = text
        match = re.search(r'不是(.+?)[，,]?是(.+)', text)
        if match:
            old, new = match[1].strip('，, '), match[2].strip('。 ')
            if old in prior['raw_text']:
                changed = prior['raw_text'].replace(old, new)
            else:
                changed = prior['raw_text'] + '，实际分量：' + new
        elif '改成' in text:
            changed = prior['raw_text'] + '，实际分量：' + text.split('改成', 1)[1]
        parsed = demo_parse(changed, [])
        if not parsed.events:
            return ExtractionResult(
                intent='correct',
                correction_id=prior['id'],
                question='请描述这条记录修正后的完整内容。',
            )
        event = parsed.events[0]
        event.occurred_at = datetime.fromisoformat(prior['occurred_at'])
        return ExtractionResult(
            intent='correct',
            events=[event],
            correction_id=prior['id'],
            question=parsed.question,
        )
    if any(word in text for word in ['不喜欢', '不吃', '过敏', '喜欢吃', '忌口', '医生要求']):
        return ExtractionResult(intent='fact', fact_text=text)
    if any(word in text for word in ['查', '回顾', '吃过什么', '吃了什么', '记录有哪些', '历史', '趋势']):
        start = end = None
        exact = re.findall(r'20\d{2}-\d{2}-\d{2}', text)
        if exact:
            start, end = date.fromisoformat(exact[0]), date.fromisoformat(exact[-1])
        elif any(word in text for word in ['今天', '昨天', '前天']):
            start = end = date_from_text(text).date()
        elif any(word in text for word in ['最近', '过去', '本周', '近一周']):
            match = re.search(r'(?:最近|过去|近)\s*(\d+)\s*天', text)
            count = min(366, int(match[1])) if match else 7
            end = now().date()
            start = end - timedelta(days=max(count, 1) - 1)
        kind = next((
            kind
            for word, kind in [
                ('饮食', 'meal'), ('吃', 'meal'), ('体重', 'weight'),
                ('睡眠', 'sleep'), ('运动', 'exercise'), ('周期', 'cycle'),
            ]
            if word in text
        ), None)
        search = next((food for food in FOODS if food in text), None)
        return ExtractionResult(
            intent='query',
            query_start=start,
            query_end=end,
            query_kind=kind,
            query_search=search,
        )
    if any(word in text for word in [
        '适合吗', '推荐', '建议', '怎么办', '可以吃', '应该吃',
        '注意什么', '怎么吃', '吃什么',
    ]):
        return ExtractionResult(intent='advice')

    # On clarification, use the original description plus the new detail.
    combined = ((original + '，补充：') if original else '') + text
    skip_question = '不知道' in text or '不清楚' in text
    events = []
    question = None
    segments = re.split(r'[，,。；;\n]', combined)
    meal = None
    meal_day = None
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        day = date_from_text(segment)
        weight = re.search(
            r'(?:体重|称重)?\s*' + NUMBER + r'\s*(公斤|千克|kg|斤)',
            segment,
            re.I,
        )
        if weight:
            value = number(weight[1]) / (2 if weight[2] == '斤' else 1)
            events.append(ExtractionEvent(
                kind='weight',
                occurred_at=day.replace(hour=7),
                raw_text=segment,
                value=value,
            ))
            continue
        if '体重' in segment or '称重' in segment:
            events.append(ExtractionEvent(
                kind='weight', occurred_at=day.replace(hour=7), raw_text=segment
            ))
            continue
        if any(word in segment for word in ['睡', '睡眠']):
            minutes = duration(segment)
            if minutes is not None:
                quality = (
                    '较差' if any(word in segment for word in ['差', '失眠', '醒'])
                    else '良好' if '好' in segment else '未说明'
                )
                events.append(ExtractionEvent(
                    kind='sleep',
                    occurred_at=day.replace(hour=23),
                    raw_text=segment,
                    value=minutes,
                    quality=quality,
                ))
            else:
                events.append(ExtractionEvent(
                    kind='sleep', occurred_at=day.replace(hour=23), raw_text=segment
                ))
                question = question or '睡了大约几小时？'
            continue
        exercise = next((
            word for word in [
                '跑步', '散步', '走路', '骑车', '骑行', '游泳',
                '力量训练', '瑜伽', '运动', '健身',
            ]
            if word in segment
        ), None)
        if exercise:
            minutes = duration(segment)
            if minutes is not None:
                intensity = (
                    'high' if any(word in segment for word in ['很累', '高强度', '剧烈'])
                    else 'low' if any(word in segment for word in ['轻松', '低强度'])
                    else 'moderate' if any(word in segment for word in ['中等', '有点累'])
                    else 'unknown'
                )
                events.append(ExtractionEvent(
                    kind='exercise',
                    occurred_at=day.replace(hour=19),
                    raw_text=segment,
                    value=minutes,
                    exercise_type=exercise,
                    intensity=intensity,
                ))
                if intensity == 'unknown' and not skip_question:
                    question = question or '这次运动感觉轻松、有点累，还是很累？'
            else:
                events.append(ExtractionEvent(
                    kind='exercise',
                    occurred_at=day.replace(hour=19),
                    raw_text=segment,
                    exercise_type=exercise,
                ))
                question = question or '这次运动持续了多少分钟？'
            continue
        if any(word in segment for word in ['月经', '经期', '生理期', '生理周期']):
            action = (
                'end' if any(word in segment for word in ['结束', '完了'])
                else 'start' if any(word in segment for word in ['开始', '来了'])
                else 'note'
            )
            events.append(ExtractionEvent(
                kind='cycle',
                occurred_at=day,
                raw_text=segment,
                cycle_action=action,
                quality=segment,
            ))
            continue
        foods = [food for food in FOODS if food in segment]
        if foods:
            slot = slot_for(segment)
            explicit_day = (
                any(word in segment for word in ['今天', '昨天', '前天'])
                or re.search(r'20\d{2}-\d{2}-\d{2}', segment)
            )
            if meal is not None and not explicit_day:
                day = meal_day
            if meal is None or (slot and slot != meal.meal_slot) or day.date() != meal_day.date():
                meal = ExtractionEvent(
                    kind='meal',
                    occurred_at=at_slot(day, slot),
                    raw_text=segment,
                    meal_slot=slot,
                    items=[],
                )
                meal_day = day
                events.append(meal)
            else:
                meal.raw_text += '，' + segment
            for food in foods:
                prefix = segment[:segment.index(food)]
                amounts = list(re.finditer(
                    NUMBER + r'\s*(?:大|小|中)?\s*(碗|份|个|杯|片|克|g|毫升|ml|块)',
                    prefix,
                    re.I,
                ))
                amount = amounts[-1] if amounts else None
                quantity = number(amount[1]) if amount else None
                unit = (
                    amount[2].lower() if amount
                    else '个' if food in ['鸡蛋', '卤蛋', '苹果', '香蕉']
                    else '份'
                )
                size = (
                    '大' if '大碗' in segment or '大份' in segment
                    else '小' if '小碗' in segment or '小份' in segment
                    else None
                )
                meal.items.append(MealItem(
                    name=food,
                    quantity=quantity,
                    unit=unit,
                    size=size,
                    cooking='油炸' if '炸' in segment else None,
                ))
                if food in ['炸鸡', '火锅', '牛肉面'] and quantity is None and not skip_question:
                    question = question or f'{food}大约吃了多少？可以说“一小碗”或“半份”；不知道也可以。'
        elif meal and any(word in segment for word in ['补充', '实际分量']):
            amount = re.search(
                NUMBER + r'\s*(?:大|小|中)?\s*(碗|份|个|杯|克|g)', segment
            )
            if amount:
                meal.items[0].quantity = number(amount[1])
                meal.items[0].unit = amount[2]
                meal.items[0].size = (
                    '小' if '小' in segment else '大' if '大' in segment else None
                )
                question = None
            meal.raw_text += '，' + segment
        elif (
            events
            and events[-1].kind == 'exercise'
            and any(word in segment for word in ['轻松', '有点累', '很累', '不知道'])
        ):
            events[-1].intensity = (
                'low' if '轻松' in segment
                else 'high' if '很累' in segment
                else 'moderate' if '有点累' in segment
                else 'unknown'
            )
            events[-1].raw_text += '，' + segment
            question = None
        elif (
            any(word in segment for word in ['吃得', '吃的'])
            and any(word in segment for word in ['咸', '油腻', '多了'])
        ):
            events.append(ExtractionEvent(
                kind='meal',
                occurred_at=at_slot(day, slot_for(segment)),
                raw_text=segment,
            ))
        elif any(word in segment for word in ['吃了', '喝了', '早餐', '午餐', '晚餐']):
            item = re.sub(
                r'^(今天|昨天|中午|早上|晚上|早餐|午餐|晚餐|吃了|喝了|我)+',
                '',
                segment,
            ).strip() or segment
            events.append(ExtractionEvent(
                kind='meal',
                occurred_at=at_slot(day, slot_for(segment)),
                raw_text=segment,
                meal_slot=slot_for(segment),
                items=[MealItem(name=item)],
            ))
    if skip_question:
        question = None
    meals = [event for event in events if event.kind == 'meal']
    if len(meals) == 1 and len(meals[0].items) == 1:
        item = meals[0].items[0]
        expressions = {
            'kcal': r'(?:热量\s*)?(\d+(?:\.\d+)?)\s*(?:kcal|千卡)',
            'protein_g': r'蛋白质\s*(\d+(?:\.\d+)?)\s*(?:克|g)',
            'carbs_g': r'(?:碳水|碳水化合物)\s*(\d+(?:\.\d+)?)\s*(?:克|g)',
            'fat_g': r'脂肪\s*(\d+(?:\.\d+)?)\s*(?:克|g)',
        }
        for key, expression in expressions.items():
            match = re.search(expression, combined, re.I)
            if match:
                setattr(item, key, float(match[1]))
                item.source = 'user'
                item.uncertainty = '用户提供的实际食用份量营养信息，未额外推算缺失字段'
        if re.search(r'(?:每\s*100\s*(?:g|克)|每百克)', combined, re.I):
            item.nutrition_basis = 'per_100g'
    return ExtractionResult(
        intent='record' if events or question else 'advice',
        events=events,
        question=question,
    )
