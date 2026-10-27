"""Independent interview exercises; no health database or model API access."""
from collections import Counter, OrderedDict, deque
from heapq import heapify, heappop, heappush
import asyncio


def two_sum(nums, target):
    seen = {}
    for i, value in enumerate(nums):
        if target - value in seen:
            return seen[target - value], i
        seen[value] = i
    return None


def merge_sorted(a, m, b, n):
    i, j, k = m - 1, n - 1, m + n - 1
    while j >= 0:
        if i >= 0 and a[i] > b[j]:
            a[k] = a[i]
            i -= 1
        else:
            a[k] = b[j]
            j -= 1
        k -= 1


def rearrange(nums):
    counts = Counter(nums)
    if max(counts.values(), default=0) > (len(nums) + 1) // 2:
        return None
    heap = [(-count, value) for value, count in counts.items()]
    heapify(heap)
    previous = (0, None)
    result = []
    while heap:
        count, value = heappop(heap)
        result.append(value)
        if previous[0] < 0:
            heappush(heap, previous)
        previous = (count + 1, value)
    return result


def level_order(root):
    if root is None:
        return []
    queue, result = deque([root]), []
    while queue:
        level = []
        for _ in range(len(queue)):
            node = queue.popleft()
            level.append(node.val)
            if node.left is not None:
                queue.append(node.left)
            if node.right is not None:
                queue.append(node.right)
        result.append(level)
    return result


class LRU:
    def __init__(self, capacity):
        if capacity < 0:
            raise ValueError('negative capacity')
        self.capacity, self.data = capacity, OrderedDict()

    def get(self, key, default=None):
        if key not in self.data:
            return default
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.capacity:
            self.data.popitem(last=False)


async def coroutine_demo():
    async def fetch(label, seconds):
        await asyncio.sleep(seconds)
        return label
    return await asyncio.gather(fetch('A', 0.01), fetch('B', 0.01))


def weather_plan():
    # Fixed, validated example plan: no arbitrary expressions or eval.
    return [
        {'id': 'a', 'kind': 'tool', 'name': 'weather',
         'args': {'city': 'Shanghai'}},
        {'id': 'b', 'kind': 'tool', 'name': 'weather',
         'args': {'city': 'Beijing'}},
        {'id': 'delta', 'kind': 'compute', 'name': 'difference',
         'args': {'left': {'ref': 'a', 'field': 'temperature_c'},
                  'right': {'ref': 'b', 'field': 'temperature_c'}}},
    ]


def execute_weather(steps):
    # Mock temperature data for exercising execution, not real weather.
    weather = {'Shanghai': 24, 'Beijing': 18}
    results = {}
    def resolve(value):
        if not isinstance(value, dict):
            return value
        if set(value) != {'ref', 'field'}:
            raise ValueError('invalid reference')
        return results[value['ref']][value['field']]
    for step in steps:
        if (set(step) != {'id', 'kind', 'name', 'args'}
                or not isinstance(step['id'], str)
                or step['id'] in results):
            raise ValueError('invalid step')
        if (step['kind'], step['name']) == ('tool', 'weather'):
            if set(step['args']) != {'city'}:
                raise ValueError('invalid weather arguments')
            results[step['id']] = {
                'temperature_c': weather[step['args']['city']]}
        elif (step['kind'], step['name']) == ('compute', 'difference'):
            if set(step['args']) != {'left', 'right'}:
                raise ValueError('invalid difference arguments')
            left, right = map(resolve, (step['args']['left'],
                                      step['args']['right']))
            results[step['id']] = {'temperature_delta_c': left - right}
        else:
            raise ValueError('unsupported step')
    return results


def verify():
    assert two_sum([3, 3], 6) == (0, 1)
    assert two_sum([], 1) is None
    a = [1, 3, 5, 0, 0, 0]
    merge_sorted(a, 3, [2, 4, 6], 3)
    assert a == [1, 2, 3, 4, 5, 6]
    a = [0]
    merge_sorted(a, 0, [1], 1)
    assert a == [1]
    for nums in ([], [1], [1, 1, 2], [1, 1, 2, 2, 3]):
        value = rearrange(nums)
        assert Counter(value) == Counter(nums)
        assert all(x != y for x, y in zip(value, value[1:]))
    assert rearrange([1, 1, 1, 2]) is None
    from types import SimpleNamespace as N
    root = N(val=1, left=N(val=2, left=None, right=None), right=None)
    assert level_order(root) == [[1], [2]]
    assert level_order(None) == []
    cache = LRU(2)
    cache.put('a', 1); cache.put('b', 2)
    assert cache.get('a') == 1
    cache.put('c', 3)
    assert cache.get('b') is None
    zero = LRU(0); zero.put('a', 1)
    assert zero.get('a') is None
    assert asyncio.run(coroutine_demo()) == ['A', 'B']
    assert execute_weather(weather_plan())['delta']['temperature_delta_c'] == 6
    try:
        execute_weather([{'id': 'x', 'kind': 'tool', 'name': 'shell', 'args': {}}])
    except ValueError:
        pass
    else:
        raise AssertionError('invalid tool accepted')
    print('All exercise checks passed.')


if __name__ == '__main__':
    verify()
