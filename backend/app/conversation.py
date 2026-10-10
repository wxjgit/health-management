"""User-reported meal feedback, separate from nutrition and permanent facts."""
import re

from .clock import date_from_text, local_day
from .schemas import MealFeedback


TOPICS = {
    'salty': r'咸|口味(?:偏|太|比较|较|有点)?重|盐(?:放|加|吃)(?:得|了)?(?:太|比较|有点)?多',
    'oily': r'油腻|太油|偏油|比较油|有点油',
    'overeating': r'吃(?:得|的)?(?:太|有点)?多了|吃撑|撑了|有点撑|很撑|太饱',
    'hungry': r'还(?:是|很)?饿|没吃饱|不饱|容易饿',
}
DENIAL = r'(?:不|没|没有)(?:是|觉得|感觉|那么|很|太|有点|特别|怎么|再){0,3}$'
ADVICE = r'怎么办|怎么(?:调整|办|吃|选|改善)|建议|推荐|应该|适合吗|可以吃|只想(?:咨询|问)|不想记录'


def wants_advice(text):
    return bool(re.search(ADVICE, text))


def explicitly_recording(text):
    return bool(re.search(r'(?<!不)(?<!别)(?:记录|记下|记一笔|帮我记)', text))


def signals_from_text(text, default_day=None):
    """Conservative coverage for explicit Chinese feedback, including negation."""
    signals = []
    for segment in re.split(r'[，,。；;\n]', text):
        segment = segment.strip()[:300]
        if not segment or re.search(r'朋友|同事|他觉得|她觉得|你觉得|比如|举例', segment):
            continue
        for topic, pattern in TOPICS.items():
            match = re.search(pattern, segment)
            if not match:
                continue
            prefix = segment[:match.start()]
            denied = re.search(DENIAL, prefix) or (topic == 'overeating' and '没吃撑' in segment)
            question = re.search(r'如果|假如|会不会|是不是', segment)
            stance = 'denial' if denied else 'question' if question else 'experience'
            explicit_date = re.search(r'今天|昨天|昨晚|前天|20\d{2}-\d{2}-\d{2}', segment)
            day = date_from_text(segment).date().isoformat() if explicit_date else default_day
            if stance == 'question':
                day = None
            signals.append(MealFeedback(topic=topic, quote=segment, day=day, stance=stance).model_dump(mode='json'))
    return signals


def reconcile_feedback(previous, supplied, text, default_day=None):
    """Only verbatim, current-user evidence may introduce or retract a concern."""
    updates = []
    for raw in supplied or []:
        signal = MealFeedback.model_validate(raw).model_dump(mode='json')
        if signal['quote'] not in text:
            continue
        verified = signals_from_text(signal['quote'], default_day)
        match = next((v for v in verified if v['topic'] == signal['topic']), None)
        if match:
            # Stance and date are checked locally, not trusted as model-derived facts.
            updates.append(match)
    updates.extend(signals_from_text(text, default_day))
    result = [MealFeedback.model_validate(f).model_dump(mode='json') for f in previous or []]
    for update in updates:
        topic = update['topic']
        result = [f for f in result if not (f['topic'] == topic and
                  (update['day'] is None or f['day'] == update['day']))]
        if update['stance'] != 'denial':
            result.append(update)
    return result[-8:]


def attach_feedback(events, feedback):
    """Link experiences only to an unambiguous meal on the matching day."""
    for event in events:
        event['feedback'] = [f for f in event.get('feedback', []) if f['stance'] == 'experience']
    for signal in feedback:
        if signal['stance'] != 'experience' or not signal['day']:
            continue
        meals = [e for e in events if e['kind'] == 'meal' and e.get('occurred_at')
                 and local_day(e['occurred_at']) == signal['day']]
        if len(meals) == 1:
            meals[0]['feedback'] = [f for f in meals[0]['feedback'] if f['topic'] != signal['topic']]
            meals[0]['feedback'].append(signal)
