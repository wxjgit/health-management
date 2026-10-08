"""Demo fixture values are deliberately identified as fixtures, not a food database."""
# Serving tuples: kcal, protein, carbs, fat. Only used in clearly labelled demo mode.
DEMO_FIXTURES = {
    '牛肉面': (550, 24, 70, 19), '米饭': (230, 4, 50, 1.5),
    '鸡胸肉': (165, 31, 0, 3.6), '鸡蛋': (75, 6.5, .5, 5), '卤蛋': (80, 6.5, 1, 5.5),
    '牛奶': (150, 8, 12, 8), '酸奶': (150, 7, 20, 4.5), '燕麦': (190, 6, 32, 4),
    '面包': (90, 3, 16, 1.5), '苹果': (95, .5, 25, .3), '香蕉': (105, 1.3, 27, .4),
    '西兰花': (35, 2.8, 7, .4), '沙拉': (180, 5, 15, 11), '炸鸡': (500, 28, 25, 32),
    '饺子': (400, 18, 55, 12), '豆腐': (100, 10, 3, 5), '面条': (300, 9, 58, 3),
    '红薯': (180, 3, 42, .3), '咖啡': (5, .3, .5, 0), '奶茶': (400, 4, 65, 14),
}
KEYS = ['kcal', 'protein_g', 'carbs_g', 'fat_g']


def estimate(draft, mode):
    if draft['kind'] != 'meal':
        return draft
    for item in draft['items']:
        if item['source'] == 'user':
            item['nutrition_source'] = '用户提供'
        elif mode == 'demo' and item['name'] in DEMO_FIXTURES:
            scale = item['quantity'] if item['quantity'] is not None else 1
            if item['unit'] in ('克', 'g', '毫升', 'ml'):
                scale /= 100 if item['name'] not in ['牛奶'] else 250
            scale *= .75 if item.get('size') == '小' else 1.3 if item.get('size') == '大' else 1
            for key, value in zip(KEYS, DEMO_FIXTURES[item['name']]):
                item[key] = round(value * scale, 1)
            item['nutrition_source'] = '演示固定值（非已核验营养库）'
            item['uncertainty'] = '演示粗估；份量与做法可能造成明显误差'
        elif item['source'] == 'model_estimate':
            item['nutrition_source'] = '模型粗估（未核验）'
        else:
            item['nutrition_source'] = '未找到营养来源'
        if item['kcal'] is not None:
            item['kcal_range'] = [round(item['kcal'] * .75), round(item['kcal'] * 1.25)] if item['source'] != 'user' else [item['kcal'], item['kcal']]
    return draft


def meal_totals(items):
    totals = {}
    for key in KEYS:
        known = [i[key] for i in items if i.get(key) is not None]
        totals[key] = round(sum(known), 1) if known else None
    totals['unknown_items'] = sum(i.get('kcal') is None for i in items)
    totals['complete_nutrition'] = all(all(i.get(key) is not None for key in KEYS) for i in items)
    totals['kcal_range'] = [round(sum(i.get('kcal_range', [i.get('kcal') or 0] * 2)[n] for i in items)) for n in [0, 1]] if totals['kcal'] is not None else None
    return totals
