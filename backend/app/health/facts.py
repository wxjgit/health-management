"""Conservative, local normalization of explicit food preferences."""
import re
import unicodedata
from datetime import timedelta

from ..common import clock
from ..contracts.facts import FactInput


def subject_key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold().strip('。！!，, ')


FACT_POLICY_VERSION = 'structured-facts-v2'
FOOD_ALIASES = {'花生米': '花生'}
VERBS = {'不喜欢吃': 'dislike', '不喜欢': 'dislike', '喜欢吃': 'like', '喜欢': 'like',
         '不能吃': 'avoid', '不吃': 'avoid', '可以接受': 'accept', '能接受': 'accept'}
PERIOD = r'这周|本周|今天|暂时|临时|最近|这两周|长期|一直|未来(?:\d{1,3}|一|二|两|三|四)周|未来(?:\d{1,3}|一|二|两|三|四)天'


def fact_period(period, today=None):
    today = today or clock.now().date()
    if period == '今天':
        return 'bounded', today
    if period in ('这周', '本周'):
        return 'bounded', today + timedelta(days=6 - today.weekday())
    match = re.fullmatch(r'未来(\d{1,3}|一|二|两|三|四)(周|天)', period or '')
    if match:
        count = int(match[1]) if match[1].isdigit() else {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4}[match[1]]
        if count < 1:
            raise ValueError('临时期限必须大于零')
        return 'bounded', today + timedelta(days=count * (7 if match[2] == '周' else 1) - 1)
    if period in ('暂时', '临时', '最近', '这两周'):
        return 'temporary_unknown', None
    return 'persistent', None


def parse_fact(content):
    text = content.strip().rstrip('。！!')
    # Full matches only: questions, hypothetical statements and mixed clauses
    # must never become a normalized confirmed preference.
    match = re.fullmatch(
        rf'(?:我)?(?P<period>{PERIOD})?(?:现在|已经)?(?P<verb>{"|".join(VERBS)})(?P<food>[^，,。！？!?；;]{{1,30}})', text)
    if not match or re.search(r'如果|假如|是不是|是否|建议|应该|可能|听说|不再|不是|但|和|还是|吗$|呢$', text):
        return FactInput(content=content)
    verb, food = match['verb'], match['food']
    value = VERBS[verb]
    today = clock.now().date()
    scope, until = fact_period(match['period'], today)
    return FactInput(content=content, type='food_preference', subject=food,
                     value=value, valid_from=today, valid_until=until, time_scope=scope,
                     restriction_reason='unknown' if value == 'avoid' else 'preference',
                     evidence=[{'field': 'subject', 'quote': food}, {'field': 'value', 'quote': verb}]
                     + ([{'field': 'time', 'quote': match['period']}] if match['period'] else []))


def enforcement(data):
    from .safety import text_risk
    if (data.get('type') in ('allergy', 'health_condition', 'professional_advice')
            or data.get('restriction_reason') in ('allergy', 'health_condition', 'professional_advice')
            or text_risk(data['content']) != 'general'):
        return 'clinical_review'
    if data.get('type') == 'restriction' or (data.get('value') == 'avoid' and data.get('restriction_reason') != 'preference'):
        return 'avoid'
    if data.get('type') == 'food_preference':
        return 'soft'
    if re.search(r'不吃|不能吃|忌口|避免', data['content']):
        return 'avoid'
    return 'unknown'


def normalize_fact(data):
    """Server-owned keys and enforcement; never trust a client-provided risk rank."""
    data = dict(data)
    if data.get('type') == 'food_preference':
        data['subject_kind'] = 'food'
    elif data.get('type') == 'health_condition':
        data['subject_kind'] = 'condition'
    else:
        data.setdefault('subject_kind', 'other')
    key = subject_key(data.get('subject', ''))
    data['subject_key'] = FOOD_ALIASES.get(key, key) if data['subject_kind'] == 'food' else key
    if data.get('time_scope', 'unknown') == 'unknown':
        data['time_scope'] = ('bounded' if data.get('valid_until') else
                              'temporary_unknown' if re.search(r'暂时|最近|这两周|临时', data['content']) else 'unknown')
    data.setdefault('assertion', 'affirmed')
    data.setdefault('restriction_reason', 'unknown')
    data.setdefault('reported_severity', 'unknown')
    data.setdefault('source_details', {})
    data.setdefault('evidence', [])
    data.setdefault('overrides', [])
    data['enforcement'] = enforcement(data)
    return data


def is_restriction(data):
    return enforcement(data) in ('avoid', 'clinical_review')


def validate_fact_content(payload, *, model_draft=False):
    """Return whether a proposal needs review, rejecting provable contradictions."""
    parsed = parse_fact(payload.content)
    if payload.type == 'food_preference' and enforcement({'type': 'legacy_text', 'content': payload.content}) == 'clinical_review':
        raise ValueError('涉及医疗限制的原文不能保存为普通偏好')
    if payload.type == 'food_preference' and parsed.type == 'food_preference':
        if subject_key(payload.subject) != subject_key(parsed.subject) or payload.value != parsed.value:
            raise ValueError('事实对象或取值与原文不一致，请核对')
        if parsed.time_scope == 'temporary_unknown' and not payload.valid_until:
            return True
        if model_draft and ((payload.valid_from and payload.valid_from != parsed.valid_from) or payload.valid_until != parsed.valid_until):
            raise ValueError('事实期限与原文不一致，请核对')
    if payload.subject and subject_key(payload.subject) not in subject_key(payload.content):
        raise ValueError('事实对象必须来自原文')
    source = payload.source_details
    if model_draft and any(value and value not in payload.content for value in (source.professional, source.institution, source.document)):
        raise ValueError('来源详情必须来自原文')
    if model_draft and source.advised_on and source.advised_on.isoformat() not in payload.content:
        raise ValueError('建议作出日期需要原文中的明确日期')
    if payload.reported_severity != 'unknown':
        terms = {'mild': '轻微|轻度', 'moderate': '中度', 'severe': '严重|重度'}
        if not re.search(terms[payload.reported_severity], payload.content):
            raise ValueError('严重程度必须来自明确自述')
    if payload.type in ('allergy', 'health_condition', 'professional_advice') and payload.assertion != 'affirmed':
        return True
    if model_draft:
        fields = {e.field for e in payload.evidence}
        if not {'subject', 'value'} <= fields:
            raise ValueError('结构化事实需要对象和取值的原文证据')
        if payload.type != parsed.type or parsed.type == 'legacy_text':
            return True  # Unsupported semantics never gain confirmation from a model alone.
    return payload.assertion != 'affirmed' or payload.time_scope == 'temporary_unknown'


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
