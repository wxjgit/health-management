"""核对小程序前端字段映射与后端真实响应是否一致。
启动方式：backend 目录下 python ../miniprograme/scripts/verify_contract.py
用合成登录态调用只读端点，打印真实结构，人工/断言比对前端 viewmodel 假设。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))

from app.main import create_app  # noqa: E402
from tests.authenticated_client import AuthenticatedClient  # noqa: E402


def keys(obj, depth=0, prefix=''):
    """递归打印 dict/list 的键结构。"""
    pad = '  ' * depth
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f'{pad}{prefix}{k}: {type(v).__name__}')
                keys(v, depth + 1)
            else:
                val = repr(v)[:50]
                print(f'{pad}{prefix}{k} = {val}')
    elif isinstance(obj, list):
        if obj:
            print(f'{pad}[0] of {len(obj)}:')
            keys(obj[0], depth + 1)
        else:
            print(f'{pad}(empty list)')


def main():
    app = create_app()
    with AuthenticatedClient(app) as c:
        # ── 写入合成数据，验证 events/dashboard 真实结构 ──
        try:
            c.put('/api/profile', json={
                'age': 32, 'sex': 'female', 'height_cm': 165, 'weight_kg': 65.0,
                'goal_weight_kg': 58.0, 'goal_date': '2027-01-01',
                'activity': 'light', 'nutrition_context': 'general',
            })
            engine = app.state.test_engine
            from app.common.clock import stamp
            # 直接经 repo 写 meal + weight 事件（绕过模型，验证 data.items 结构）
            engine.repo.save_events([{
                'id': 'test-evt-1', 'kind': 'meal',
                'occurred_at': stamp(), 'raw_text': '中午吃了一大碗牛肉面，加了一个卤蛋',
                'data': {
                    'meal_slot': '午餐',
                    'items': [
                        {'name': '牛肉面', 'quantity': 1, 'unit': '碗', 'size': '大碗',
                         'kcal': 590, 'protein_g': 24, 'carbs_g': 78, 'fat_g': 18,
                         'nutrition_status': 'estimated',
                         'nutrition_sources': {'kcal': 'model_estimate', 'protein_g': 'model_estimate',
                                                'carbs_g': 'model_estimate', 'fat_g': 'model_estimate'}},
                        {'name': '卤蛋', 'quantity': 1, 'unit': '个', 'size': None,
                         'kcal': 90, 'protein_g': 7, 'carbs_g': 1, 'fat_g': 6,
                         'nutrition_status': 'user_provided',
                         'nutrition_sources': {'kcal': 'user', 'protein_g': 'user',
                                                'carbs_g': 'user', 'fat_g': 'user'}},
                    ],
                    'feedback': [{'type': 'salty', 'stance': 'affirmed', 'quote': '有点咸'}],
                },
            }, {
                'id': 'test-evt-2', 'kind': 'weight',
                'occurred_at': stamp(), 'raw_text': '今天早上体重65公斤',
                'data': {'value': 65.0, 'unit': 'kg'},
            }], op_key='verify-contract-op-1')
            # 再写一次同 id 触发修订（验证 revisions.changes 结构）
            engine.repo.save_events([{
                'id': 'test-evt-1', 'kind': 'meal',
                'occurred_at': stamp(), 'raw_text': '不是大碗，是小碗',
                'data': {
                    'meal_slot': '午餐',
                    'items': [
                        {'name': '牛肉面', 'quantity': 1, 'unit': '碗', 'size': '小碗',
                         'kcal': 480, 'protein_g': 20, 'carbs_g': 62, 'fat_g': 15,
                         'nutrition_status': 'estimated',
                         'nutrition_sources': {'kcal': 'model_estimate', 'protein_g': 'model_estimate',
                                                'carbs_g': 'model_estimate', 'fat_g': 'model_estimate'}},
                        {'name': '卤蛋', 'quantity': 1, 'unit': '个', 'size': None,
                         'kcal': 90, 'protein_g': 7, 'carbs_g': 1, 'fat_g': 6,
                         'nutrition_status': 'user_provided',
                         'nutrition_sources': {'kcal': 'user', 'protein_g': 'user',
                                                'carbs_g': 'user', 'fat_g': 'user'}},
                    ],
                },
            }], op_key='verify-contract-op-2', correction_id='test-evt-1',
               expected_version=1, user_correction=True)
            print('✓ 已写入合成 meal(+修订) + weight 事件')
        except Exception as e:  # noqa: BLE001
            print(f'写入合成数据失败（不影响只读核对）: {e}')

        endpoints = [
            ('GET', '/api/profile'),
            ('GET', '/api/events?limit=5'),
            ('GET', '/api/dashboard'),
            ('GET', '/api/events/test-evt-1/revisions'),
            ('GET', '/api/auth/usage'),
            ('GET', '/api/targets/default'),
        ]
        for method, path in endpoints:
            print('\n' + '=' * 60)
            print(f'{method} {path}')
            print('=' * 60)
            try:
                r = c.get(path)
                print(f'status={r.status_code}')
                if r.status_code == 200:
                    keys(r.json())
                else:
                    print(r.text[:200])
            except Exception as e:  # noqa: BLE001
                print(f'ERROR: {e}')


if __name__ == '__main__':
    main()
