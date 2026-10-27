"""Local authority for safety routing and operation eligibility.

Rules prioritize recall; neither a valid schema nor an unmatched phrase proves
medical safety. This module never calls a model or saves a diagnosis.
"""
import hashlib
import json
import re
import unicodedata

from ..contracts.safety import SafetyClassification
from .fact_resolution import pending_decisions

POLICY_VERSION = 'hybrid-safety-facts-v2'
PROMPT_VERSION = 'safety-classifier-v1'
PRIORITY = ['emergency', 'eating_disorder', 'medical', 'special_population', 'uncertain', 'general']
REPLIES = {
    'emergency': '你提到的情况可能涉及立即危险。请立即联系当地急救服务或前往急诊，请可信任的人陪同，不要自行驾车或等待饮食建议。这里不能替代急救评估。',
    'medical': '这涉及身体不适、疾病、用药或特殊医疗营养，请咨询医生或临床营养师。这里不能判断病因或调整药物，本次暂停营养估算、热量优化和饮食运动计划。若症状严重、迅速加重或出现胸痛、呼吸困难、意识异常，请立即寻求急救。',
    'eating_disorder': '你提到的进食困扰或补偿行为值得认真对待，这不是意志力问题。请尽快联系医生或熟悉进食障碍的专业人员，也可以请可信任的人陪同。本次暂停热量、减重目标和计划，不提供催吐、滥用泻药、禁食或补偿性运动的方法。若已有晕厥、胸痛、意识异常或严重脱水，请立即寻求急救。',
    'special_population': '这涉及孕产期、哺乳期或未成年人的营养需求，请由医生或合格营养师按具体阶段评估。本次不提供通用成人营养估算、减重目标和饮食运动计划。未成年人可以请可信任的成年人陪同咨询。',
    'uncertain': '目前无法可靠完成本次安全判断，先暂停营养估算、热量优化、普通建议和计划生成。你可以补充情况；涉及身体不适时请咨询医生，存在立即危险时请寻求当地急救。',
}
RULES = {
    'emergency': r'胸痛|胸口.{0,5}(?:痛|压迫)|呼吸.{0,4}困难|喘不过气|无法呼吸|窒息|晕厥|晕倒|昏迷|意识(?:模糊|不清|异常)|叫不醒|抽搐|吐血|呕血|便血|黑便|严重脱水|大出血|(?:孕|产后).{0,20}(?:出血|剧痛)|自杀|自残|不想活|服药过量|药物过量|中毒|chestpain|shortnessofbreath|overdose',
    'eating_disorder': r'进食障碍|饮食障碍|厌食症|暴食|贪食症|催吐|抠吐|泻药|补偿性|(?:运动|跑步).{0,12}(?:抵消|补偿)|(?:吃|餐).{0,12}(?:内疚|罪恶|吐出来)|(?:不吃|少吃|跳餐|挨饿).{0,16}(?:补偿|抵消|补回来)|害怕.{0,5}(?:吃|变胖)|(?:不吃|禁食|绝食).{0,8}天|(?:每天|每日|一天|只吃).{0,8}(?<!\d)[1-9]\d{0,2}(?:kcal|千卡|大卡)|bulimia|anorexia|purging|eatingdisorder',
    'medical': r'头.{0,3}晕|眩晕|眼前发黑|低血糖|血糖.{0,3}低|血压|糖尿病|肾病|肾功能|心脏|心慌|心悸|脱水|腹泻|拉肚子|拉稀|持续.{0,10}(?:痛|吐|拉)|呕吐|腹痛|胃痛|头痛|发热|发烧|乏力|手抖|恶心|过敏|水肿|营养不良|低体重|疾病|慢性病|治疗|透析|术后|手术|肿瘤|癌|痛风|贫血|用药|吃药|服药|停药|换药|药量|药物|剂量|胰岛素|降糖药|二甲双胍|司美格鲁肽|利尿|排盐|排毒|医疗营养|管饲|医生.{0,8}(?:要求|建议|规定)|营养师.{0,8}(?:要求|建议|规定)|dizz|hypoglyc|diarrh|diabet|medication',
    'special_population': r'孕|哺乳|产后|产褥|月子|母乳|未成年|儿童|婴儿|宝宝|小孩|孩子|小学生|初中|高中生|青少年|未满(?:18|十八)岁|(?<!\d)(?:[0-9]|1[0-7])(?:周)?岁|(?<![零一二三四五六七八九十两])(?:[一二三四五六七八九两]|十[一二三四五六七]?)岁|pregnan|breastfeed|postpartum|(?<!\d)(?:[0-9]|1[0-7])yearsold',
    'uncertain': r'身体不适|身体不舒服|不对劲|疑似|不明原因|症状|病情|健康风险|特殊.{0,4}(?:医疗|营养)|病|症|药',
}
RULES = {key: re.compile(value, re.I) for key, value in RULES.items()}


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if unicodedata.category(c)[0] not in 'ZPC')


def decision(category='general', reason='classified', **extra):
    return {'category': category, 'reason': reason, 'policy_version': POLICY_VERSION, **extra}


def text_risk(text):
    # Deliberately conservative: explicit local signals cannot be waived by a model.
    value = normalize(text)
    return next((key for key in PRIORITY if key in RULES and RULES[key].search(value)), 'general')


def local_safety(repo, texts=()):
    profile = repo.get_meta('profile') or {}
    categories = [text_risk(text) for text in texts]
    categories.extend('medical' if fact.get('enforcement') == 'clinical_review'
                      else text_risk(fact['content']) for fact in repo.safety_restrictions())
    view = repo.resolve_facts(purpose='safety')
    if view['conflicts'] or pending_decisions(view) or len(view['constraints']) > 8:
        categories.append('uncertain')
    age = profile.get('age')
    if (isinstance(age, int) and age < 18) or profile.get('nutrition_context') in ('pregnant', 'breastfeeding'):
        categories.append('special_population')
    if profile.get('nutrition_context') == 'clinical':
        categories.append('medical')
    category = min(categories, key=PRIORITY.index) if categories else 'general'
    return decision(category, 'local_rule' if category != 'general' else 'no_local_signal')


def merge_classification(result, context):
    parsed = SafetyClassification.model_validate(result)
    sources = {source['source_id']: source['text'] for source in context['sources']}
    categories = []
    for finding in parsed.findings:
        evidence = finding.evidence
        if evidence.source_id not in sources or evidence.quote not in sources[evidence.source_id]:
            raise ValueError('安全分类证据不在已授权原文中')
        if finding.assertion != 'denied':
            # Hypothetical/past medical questions still get a bounded response;
            # only an affirmed current emergency gets an emergency instruction.
            category = finding.category
            if category == 'emergency' and (finding.assertion != 'affirmed' or finding.timeframe != 'current'):
                category = 'medical'
            categories.append(category)
    if parsed.uncertain:
        categories.append('uncertain')
    return decision(min(categories, key=PRIORITY.index) if categories else 'general', 'model_classification')


def fingerprint(context, profile, facts, model):
    value = [POLICY_VERSION, PROMPT_VERSION, model, context, profile, facts]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def plan_eligibility(repo, targets=None, note=''):
    result = local_safety(repo, [note])
    if result['category'] != 'general':
        return result
    # Product limits on manual plans, independent of model classification.
    if targets and (not 1500 <= targets['kcal'] <= 3500 or targets.get('exercise_minutes', 0) > 300):
        return decision('uncertain', 'unsupported_manual_targets')
    return result


def require_plan_eligibility(repo, targets=None, note=''):
    result = plan_eligibility(repo, targets, note)
    if result['category'] != 'general':
        raise ValueError(REPLIES[result['category']])


def unsafe_generated_text(text):
    """Secondary output guard, not a general medical truth verifier."""
    return text_risk(text) != 'general' or bool(re.search(
        r'催吐|抠吐|泻药|补偿性|断食|禁食|跳餐|饿一天|抵消.{0,10}(?:摄入|热量)|'
        r'跳过.{0,4}(?:餐|饭)|只喝(?:白)?水|不吃饭|'
        r'不吃(?:主食|晚饭|晚餐|早餐)|(?:加|减|停|换)药|药量|剂量|胰岛素|'
        r'诊断为|治疗方案|治愈|排盐|排毒|低钠盐|代盐', text))


def offline_record(text, pending=False):
    """Full-string allowlist: unconsumed text never receives offline clearance."""
    from ..parsing.demo import FOODS
    vocabulary = sorted([*FOODS, '今天', '昨天', '前天', '早上', '早晨', '中午', '午餐', '早餐', '晚餐',
                         '晚饭', '昨晚', '晚上', '体重', '称重', '记录', '查询', '饮食', '餐食', '的', '早',
                         '吃了', '吃', '喝了', '喝', '睡了', '睡', '跑步', '运动', '轻松', '一般', '很累',
                         '分钟', '小时', '公斤', '千克', 'kg', 'kcal', '千卡', '热量', '蛋白质', '碳水',
                         '脂肪', '克', 'g', '碗', '份', '个', '杯', '片', '一', '二', '两', '三', '四', '五',
                         '六', '七', '八', '九', '十', '半', '小', '大', '加了', '加', '和', '是', '不是',
                         '改成', '修改', '纠正', '不清楚', '不知道', '先不记', '不记了', '取消记录',
                         '有点', '咸', '油腻', '饿', '撑'], key=len, reverse=True)
    pattern = re.compile('|'.join(map(re.escape, vocabulary)) + r'|\d+(?:\.\d+)?|[\s，。,.、：:/\-]', re.I)
    clean = text.strip()
    cursor = 0
    while cursor < len(clean):
        match = pattern.match(clean, cursor)
        if match is None:
            return False
        cursor = match.end()
    return bool(clean and (pending or re.search(r'吃|喝|体重|称重|睡|跑步|运动|记录|查询|修改|改成|不是', clean)))


def offline_preference(text):
    if not text.strip():
        return False
    from ..health.facts import parse_fact
    from ..parsing.demo import FOODS
    parsed = parse_fact(text)
    return parsed.type == 'food_preference' and parsed.subject in FOODS
