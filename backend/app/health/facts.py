"""Conservative, local normalization of explicit food preferences."""
import re
import unicodedata
from datetime import timedelta

from ..common import clock
from ..contracts.facts import FactInput


def subject_key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold().strip('。！!，, ')


def parse_fact(content):
    text = content.strip().rstrip('。！!')
    # Full matches only: questions, hypothetical statements and mixed clauses
    # must never become a normalized confirmed preference.
    match = re.fullmatch(
        r'(?:我)?(?P<period>这周|本周|今天)?(?:现在|已经)?(?P<verb>不喜欢|喜欢吃|喜欢|不吃|可以接受|能接受)(?P<food>[^，,。！？!?；;]{1,30})', text)
    if not match or re.search(r'如果|假如|是不是|是否|建议|应该|可能|听说|不再|不是|但|和|还是|吗$|呢$', text):
        return FactInput(content=content)
    verb, food = match['verb'], match['food']
    value = {'不喜欢': 'dislike', '喜欢吃': 'like', '喜欢': 'like', '不吃': 'avoid',
             '可以接受': 'accept', '能接受': 'accept'}[verb]
    today = clock.now().date()
    until = (today if match['period'] == '今天' else
             today + timedelta(days=6 - today.weekday()) if match['period'] else None)
    return FactInput(content=content, type='food_preference', subject=subject_key(food),
                     value=value, valid_from=today, valid_until=until)


def is_restriction(data):
    from .safety import text_risk
    return data.get('type') in ('allergy', 'restriction', 'professional_advice') or text_risk(data['content']) != 'general'


def uncertain_assertion(content, context=None):
    source = context or content
    start = source.find(content)
    clause = source
    if start >= 0:
        separators = '，,。！？!?；;\n'
        left = max(source.rfind(char, 0, start) for char in separators)
        ends = [index for char in separators if (index := source.find(char, start + len(content))) >= 0]
        clause = source[left + 1:min(ends) + 1 if ends else len(source)]
    return bool(re.search(r'如果|假如|可能|是不是|是否|听说|应该|[?？]|吗[。！!]?$', clause)
                or re.match(r'\s*(?:我的)?(?:朋友|同事|家人|别人|他|她|你)', clause))


def fact_receipt(record, content):
    if not record:
        return '这条长期信息已不存在，请重新核对。'
    if record['confirmation'] == 'candidate':
        return '已保留为待确认信息，暂不用于建议。请在设置中核对：' + content
    if record['confirmation'] == 'conflict':
        return '这条信息与已有信息存在冲突，暂不用于普通建议。请在设置中选择：' + content
    if record['lifecycle'] == 'scheduled':
        return f'已保存，将在 {record["valid_from"]} 生效：' + content
    if record['lifecycle'] not in ('active', 'needs_review'):
        return '这条长期信息当前已失效，不用于建议：' + content
    return '已保存为你明确提供的长期信息，后续建议会参考它：' + content
