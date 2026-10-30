from __future__ import annotations

import ast
import collections
import hashlib
import html
import json
import re
import textwrap
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
    Preformatted, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from rewrite_answers import ANSWERS

ROOT = Path(r'C:\Users\wxj_1\Desktop\health-management')
SOURCE = Path(__file__).resolve().with_name('source_questions_original.pdf')
OUT = ROOT / 'output' / 'pdf'
PDF = OUT / 'Agent开发面试题_全量参考答案_易懂版_按轻记工程逐题重写.pdf'
DATA = OUT / 'source' / 'questions_and_answers.json'
MD = OUT / 'source' / 'Agent开发面试题_逐题工程版.md'
MANIFEST = OUT / 'source' / 'verification.json'
W, H = A4
NAVY = colors.HexColor('#173650')
BLUE = colors.HexColor('#0B66A3')
INK = colors.HexColor('#243347')
GRAY = colors.HexColor('#61758A')
LINE = colors.HexColor('#D2DDE8')
PALE = colors.HexColor('#F1F7FC')
TEAL = colors.HexColor('#06766F')

pdfmetrics.registerFont(TTFont('CN', r'C:\Windows\Fonts\msyh.ttc', subfontIndex=0))
pdfmetrics.registerFont(TTFont('CNBold', r'C:\Windows\Fonts\msyhbd.ttc', subfontIndex=0))
pdfmetrics.registerFont(TTFont('Code', r'C:\Windows\Fonts\consola.ttf'))
pdfmetrics.registerFontFamily('CN', normal='CN', bold='CNBold', italic='CN', boldItalic='CNBold')

ST = {
    'title': ParagraphStyle('title', fontName='CNBold', fontSize=27, leading=39, textColor=NAVY, alignment=TA_CENTER, spaceAfter=16, wordWrap='CJK'),
    'subtitle': ParagraphStyle('subtitle', fontName='CN', fontSize=12, leading=21, textColor=GRAY, alignment=TA_CENTER, spaceAfter=14, wordWrap='CJK'),
    'h1': ParagraphStyle('h1', fontName='CNBold', fontSize=20, leading=29, textColor=BLUE, spaceBefore=4, spaceAfter=14, keepWithNext=True, wordWrap='CJK'),
    'h2': ParagraphStyle('h2', fontName='CNBold', fontSize=14, leading=22, textColor=NAVY, spaceBefore=12, spaceAfter=9, keepWithNext=True, wordWrap='CJK'),
    'body': ParagraphStyle('body', fontName='CN', fontSize=10, leading=17.5, textColor=INK, spaceAfter=7, wordWrap='CJK', allowWidows=0, allowOrphans=0),
    'q': ParagraphStyle('q', fontName='CNBold', fontSize=10.8, leading=17.5, textColor=NAVY, spaceAfter=6, keepWithNext=True, wordWrap='CJK'),
    'answer': ParagraphStyle('answer', fontName='CN', fontSize=10, leading=17.2, textColor=INK, leftIndent=8, spaceAfter=6, wordWrap='CJK', allowWidows=0, allowOrphans=0),
    'small': ParagraphStyle('small', fontName='CN', fontSize=8, leading=13, textColor=GRAY, spaceAfter=6, wordWrap='CJK'),
    'evidence': ParagraphStyle('evidence', fontName='CN', fontSize=7.5, leading=12, textColor=GRAY, leftIndent=8, spaceAfter=12, wordWrap='CJK'),
    'th': ParagraphStyle('th', fontName='CNBold', fontSize=9.2, leading=15, textColor=colors.white, wordWrap='CJK'),
    'td': ParagraphStyle('td', fontName='CN', fontSize=9.2, leading=15, textColor=INK, wordWrap='CJK'),
    'code': ParagraphStyle('code', fontName='Code', fontSize=8, leading=11, textColor=INK, backColor=colors.HexColor('#F4F6F8'), borderPadding=8, spaceAfter=12),
}


def P(text, style='body', raw=False):
    return Paragraph(text if raw else html.escape(text), ST[style])


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def extract():
    reader = PdfReader(str(SOURCE))
    # The source's numbered company questions occupy PDF pages 8-88.
    body = '\n'.join('\n'.join((page.extract_text() or '').splitlines()[2:])
                     for page in reader.pages[7:88])
    token = re.compile(
        r'(?m)^(?P<company>[一二三四五六七八九十]+、[^\n]+)\s*$'
        r'|^(?P<round>(?:方向[12])?[一二三]面)\s*$'
        r'|^(?P<number>\d+)\.\s+'
    )
    matches = list(token.finditer(body))
    company = round_name = None
    rows = []
    for i, match in enumerate(matches):
        if match.group('company'):
            company = match.group('company').strip()
        elif match.group('round'):
            round_name = match.group('round').strip()
        else:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            part = body[match.end():end]
            if '参考答案：' not in part:
                raise ValueError(f'missing source answer boundary {len(rows)+1}')
            q = clean(part.split('参考答案：', 1)[0])
            gid = len(rows) + 1
            if not company or not round_name or not q:
                raise ValueError(f'bad heading/question {gid}')
            rows.append(dict(gid=gid, number=int(match.group('number')), company=company,
                             round=round_name, question=q, **ANSWERS[gid]))
    assert len(rows) == 642, len(rows)
    assert set(ANSWERS) == set(range(1, 643))
    assert len({r['company'] for r in rows}) == 11
    for _, group in __import__('itertools').groupby(rows, key=lambda r: (r['company'], r['round'])):
        group = list(group)
        assert [r['number'] for r in group] == list(range(1, len(group)+1)), group[0]
    return rows


class InterviewDoc(BaseDocTemplate):
    def __init__(self, path, **kwargs):
        super().__init__(str(path), pagesize=A4, leftMargin=21*mm, rightMargin=21*mm,
                         topMargin=22*mm, bottomMargin=20*mm,
                         title='Agent开发面试题：642题按轻记工程逐题重写', author='Codex', **kwargs)
        self.chapter = '轻记工程版'
        self.page_map = {}
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                      leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
        self.addPageTemplates(PageTemplate(id='default', frames=frame, onPage=self.decorate))

    def decorate(self, canv, doc):
        canv.saveState()
        canv.setFont('CN', 7.6)
        canv.setFillColor(GRAY)
        if doc.page > 1:
            canv.drawString(21*mm, H-13*mm, 'Agent 开发面试题 · 全量工程参考答案')
            canv.drawRightString(W-21*mm, H-13*mm, self.chapter)
            canv.setStrokeColor(LINE)
            canv.line(21*mm, H-16*mm, W-21*mm, H-16*mm)
        canv.setStrokeColor(LINE)
        canv.line(21*mm, 15*mm, W-21*mm, 15*mm)
        canv.drawString(21*mm, 10*mm, '轻记 · 个人减脂 Agent | 2026-10-19 工程快照')
        canv.drawRightString(W-21*mm, 10*mm, str(doc.page))
        canv.restoreState()

    def beforeDocument(self):
        self.chapter = '轻记工程版'
        self.page_map = {}

    def afterFlowable(self, flowable):
        if getattr(flowable, '_heading', None):
            level, title, key = flowable._heading
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level=level, closed=False)
            self.notify('TOCEntry', (level, title, self.page, key))
            if level == 0:
                self.chapter = title
                if self.page > 1:
                    # The page header is drawn before the first body heading.
                    self.canv.saveState()
                    self.canv.setFillColor(colors.white)
                    self.canv.rect(W-83*mm, H-14.5*mm, 62*mm, 6*mm, fill=1, stroke=0)
                    self.canv.setFillColor(GRAY)
                    self.canv.setFont('CN',7.6)
                    self.canv.drawRightString(W-21*mm,H-13*mm,title)
                    self.canv.restoreState()
        if getattr(flowable, '_gid', None):
            self.page_map[flowable._gid] = self.page


def heading(text, key, level=0):
    para = P(text, 'h1' if level == 0 else 'h2')
    para._heading = (level, text, key)
    return para


def table(rows, widths):
    cells = [[P(v, 'th' if i == 0 else 'td') for v in row] for i,row in enumerate(rows)]
    obj = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
    obj.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),NAVY),
        ('GRID',(0,0),(-1,-1),.4,LINE),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,PALE]),
    ]))
    return obj


APPENDICES = [
('A', 'Two Sum（独立算法，工程未使用）', '''
def two_sum(nums, target):
    seen = {}
    for i, x in enumerate(nums):
        if target - x in seen:
            return [seen[target - x], i]
        seen[x] = i
    return None
''', '先查询再记录当前元素，支持重复数值且不会使用同一个下标两次。'),
('B', '原地合并有序数组（独立算法）', '''
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
''', 'a 必须预留 m+n 长度。当前轻记按 SQL 排序，不依赖这个函数。'),
('C', '受限 ReAct Loop（扩展示意）', '''
def bounded_loop(state, model, tools, memory, limit=8):
    for turn in range(limit):
        context = memory.read_for(state)
        decision = model.decide(state, context, tools.schemas())
        if decision.kind == "final":
            validate_completion(state, decision)
            return decision.answer
        tool = tools.authorize(decision.name, state.identity)
        args = tool.validate(decision.arguments)
        key = stable_operation_key(state.run_id, turn)
        result = tool.execute(args, idempotency_key=key)
        state = checkpoint_result(state, key, result)
    raise RuntimeError("task budget exhausted")
''', '这是伪代码，不是现有 Engine。保存健康事实必须调用原有校验服务；未知副作用不能盲目重试。'),
('D', 'Plan-and-Execute：两城市温差（注入工具的示例）', '''
def weather_plan():
    return [
        {"id": "a", "tool": "weather", "city": "Shanghai"},
        {"id": "b", "tool": "weather", "city": "Beijing"},
        {"id": "d", "compute": "difference", "deps": ["a", "b"]},
    ]

def execute_weather(plan, weather):
    results = {}
    for step in plan:
        if step.get("tool") == "weather":
            value = weather(step["city"])
            if type(value) not in (int, float):
                raise ValueError("invalid temperature")
            results[step["id"]] = value
        else:
            a, b = step["deps"]
            results[step["id"]] = results[a] - results[b]
    return results
''', 'weather 是注入的只读工具，没有实际天气服务。实际工程计划是健康目标，不是自主任务 Planner。'),
('E', '重复元素重排且相邻不同', '''
from collections import Counter
import heapq

def rearrange(nums):
    counts = Counter(nums)
    if max(counts.values(), default=0) > (len(nums) + 1) // 2:
        return None
    heap = [(-c, value) for value, c in counts.items()]
    heapq.heapify(heap)
    answer = []
    previous = (0, None)
    while heap:
        count, value = heapq.heappop(heap)
        answer.append(value)
        if previous[0] < 0:
            heapq.heappush(heap, previous)
        previous = (count + 1, value)
    return answer
''', '示例假设元素可比较。将刚取出的元素延迟放回，防止连续使用。轻记未使用此调度算法。'),
('F', '二叉树层序遍历', '''
from collections import deque

def level_order(root):
    if root is None:
        return []
    queue, result = deque([root]), []
    while queue:
        layer = []
        for _ in range(len(queue)):
            node = queue.popleft()
            layer.append(node.val)
            if node.left:
                queue.append(node.left)
            if node.right:
                queue.append(node.right)
        result.append(layer)
    return result
''', '标准树遍历，未补造原面试中的输入条件。LangGraph 调度不是二叉树 BFS。'),
('G', 'LRU Cache', '''
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        if capacity < 0:
            raise ValueError("capacity must be nonnegative")
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key):
        if key not in self.data:
            return -1
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.capacity:
            self.data.popitem(last=False)
''', '这是单线程示例。若缓存轻记汇总，应把 data_version 纳入缓存键，避免纠错后读旧结果。'),
('H', '线程池执行流程（伪代码）', '''
submit(task):
    if state != RUNNING: reject(task)
    else if worker_count < core: start_worker(task)
    else if queue.offer(task): ensure_worker_exists()
    else if worker_count < maximum: start_worker(task)
    else: reject(task)

worker_loop():
    while can_run_or_drain():
        task = blocking_take()
        try: complete_future(task.run())
        catch error: fail_future(error)
        finally: update_metrics()
''', '完整实现还需关闭、取消、队列边界和异常隔离。轻记没有自建线程池。'),
('I', 'Python 协程', '''
import asyncio

async def fetch(name, delay):
    await asyncio.sleep(delay)
    return name + " done"

async def main():
    values = await asyncio.gather(
        fetch("a", 0.02), fetch("b", 0.01)
    )
    return values

if __name__ == "__main__":
    print(asyncio.run(main()))
''', 'gather 返回结果顺序与参数顺序一致。轻记后台用 asyncio.to_thread 调用同步 tick。'),
('J', 'Java 两种单例', '''
public enum Singleton {
    INSTANCE;
}

final class DclSingleton {
    private static volatile DclSingleton instance;
    private DclSingleton() {}

    public static DclSingleton getInstance() {
        if (instance == null) {
            synchronized (DclSingleton.class) {
                if (instance == null) {
                    instance = new DclSingleton();
                }
            }
        }
        return instance;
    }
}
''', 'volatile 保障安全发布；锁住类级初始化并在锁内再次判空。轻记 Engine 由 FastAPI 生命周期创建。'),
]


def make_story(rows):
    story = [Spacer(1, 30*mm), P('Agent 开发面试题', 'title'),
             P('全量参考答案 · 易懂版', 'subtitle'),
             P('642 道题 · 全部按轻记工程逐题重写', 'subtitle'),
             Spacer(1, 8*mm)]
    story.append(table([
        ['本版内容', '回答依据'],
        ['工程主题', '轻记 · 个人减脂 Agent，本地单用户网页原型'],
        ['原题结构', '保留 11 家公司、轮次、原题号和 642 道问题'],
        ['改写方式', '逐题替换全部参考答案；同类题在原位置完整作答'],
        ['可信边界', '已实现、扩展设计、个人事实待补分别标明'],
        ['资料日期', '2026-10-19；依据当前工作区源码核对'],
    ], [32*mm, 136*mm]))
    story += [Spacer(1, 12*mm), P('原题中的平台、客户或团队预设不等于本项目事实。涉及多 Agent、MCP、RAG、DSL、沙箱与大规模并发时，回答从轻记当前实现出发，再说明可行的扩展方法。算法题保留标准解法，并解释与工程的联系及边界。', 'body'), PageBreak()]

    story.append(heading('使用说明与工程事实', 'facts'))
    story += [P('本资料用于把题库转换成同一项目的面试表达。技术回答可以直接围绕工程讲；个人任职、晋升、薪酬、城市和客户数无法从代码得出，需由本人补充。不要把“当前没有”说成“已上线”，也不要把扩展方案表述为既有经历。'),
              table([
                  ['领域', '工程实际情况'],
                  ['技术栈', 'React/TypeScript/Vite；FastAPI/Pydantic；LangGraph；SQLite'],
                  ['三张图', 'Chat：记录、纠错、查询、咨询、事实；Plan：提出、确认、激活；Analysis：规律与复盘'],
                  ['事实与状态', 'health.db 是业务事实库；checkpoints.db 是图状态库，两者不是同一原子事务'],
                  ['模型边界', '解析、补丁、缺失营养粗估、自然表达由模型辅助；计算、写入和确认由本地程序控制'],
                  ['可靠性', 'request_id 指纹、恢复 receipts、稳定事件操作键、事务和计划版本检查'],
                  ['实际部署', 'start.ps1 启动本机服务；无多用户、独立 Worker 池、设备推送或文档 RAG'],
              ], [32*mm,136*mm]),
              Spacer(1,6*mm),
              P('重点核对：上下文与记忆', 'h2'),
              P('当前没有按 Token 水位自动压缩。解析选近期 12 条候选；咨询按今天、7 天或 28 天组织结构化数据，仅有指代时带最近两条用户消息。未选入提示词的原始事件仍可查；完成流程上下文超过 30 天清理，待补超过 366 天清理；一年后完整过期月份转摘要并删除逐餐细节。删除与归档的信息不能承诺自动找回。'),
              P('重点核对：验证与限制', 'h2'),
              P('仓库有后端 124 个收集到的测试用例和前端 10 个交互测试；这些数字是覆盖范围，不代表本次重跑了全部测试，也不代表生产容量或医学效果。模型密钥存在硬编码配置风险，资料不展示任何密钥；计划目标与营养估算也不能表述为临床处方。'),
              P('阅读每题末尾的代码依据，可以回到工程核查。文中路径相对 health-management 工程根目录；扩展设计没有对应实现时会明确标注。', 'small'), PageBreak()]
    story.append(P('目录', 'h1'))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle('toc0',fontName='CNBold',fontSize=10.5,leading=16,spaceBefore=5,textColor=NAVY,wordWrap='CJK'),
        ParagraphStyle('toc1',fontName='CN',fontSize=9,leading=13,leftIndent=15,spaceBefore=0,textColor=GRAY,wordWrap='CJK'),
    ]
    toc.dotsMinLevel = 0
    story += [toc, PageBreak()]

    prev_company = prev_round = None
    pending_headings = []
    md = ['# Agent 开发面试题：642 题按轻记工程逐题重写', '', '工程快照：2026-10-19', '']
    for row in rows:
        if row['company'] != prev_company:
            if prev_company is not None:
                story.append(PageBreak())
            pending_headings.append(heading(row['company'], f"company-{row['gid']}"))
            md += ['# '+row['company'], '']
            prev_company = row['company']
            prev_round = None
        if row['round'] != prev_round:
            pending_headings.append(heading(row['round'], f"round-{row['gid']}", 1))
            md += ['## '+row['round'], '']
            prev_round = row['round']
        label = f"{row['number']}. {row['question']}"
        q = P(label, 'q')
        q._gid = row['gid']
        answer = P('<b>参考答案：</b>' + html.escape(row['text']), 'answer', raw=True)
        meta = P(f"[Q{row['gid']:03d} · {row['status']}] 依据：{row['evidence']}", 'evidence')
        story.append(KeepTogether([*pending_headings, q, answer, meta]))
        pending_headings = []
        md += ['### '+label, '', '**参考答案：**'+row['text'], '',
               f"依据：{row['evidence']} | {row['status']} | Q{row['gid']:03d}", '']

    story += [PageBreak(), heading('编码题参考实现与工程关联', 'appendices')]
    for letter,title,code,note in APPENDICES:
        story.append(KeepTogether([
            heading(f'附录 {letter}：{title}', f'appendix-{letter}', 1),
            P(note),
            Preformatted(textwrap.dedent(code).strip(), ST['code'], maxLineLength=83),
            Spacer(1,4*mm),
        ]))
        md += [f'## 附录 {letter}：{title}', '', note, '', '```', textwrap.dedent(code).strip(), '```', '']

    story += [PageBreak(), heading('证据索引与口述边界', 'references'),
              table([
                  ['主题', '核查入口'],
                  ['API、错误、生命周期', 'backend/app/main.py'],
                  ['图、恢复、幂等、State', 'backend/app/graphs.py'],
                  ['草稿、严格事件、补丁', 'backend/app/schemas.py；drafts.py'],
                  ['模型封装与各类提示', 'backend/app/parser.py'],
                  ['营养指纹、单位、来源', 'backend/app/nutrition.py'],
                  ['上下文、建议、反馈', 'backend/app/advice.py；replies.py；conversation.py'],
                  ['计划、统计、归档', 'backend/app/services.py；targets.py'],
                  ['事实、修订、回执', 'backend/app/repository.py'],
                  ['后台任务与保留期', 'backend/app/scheduler.py；graphs.py: prune_context'],
                  ['前端、测试与文档', 'frontend/src/；backend/tests/；README.md；docs/开发文档.md'],
              ], [48*mm,120*mm]),
              Spacer(1,7*mm),
              P('基础概念的官方核查资料', 'h2')]
    links = [
        ('LangGraph interrupt 参考', 'https://reference.langchain.com/python/langgraph/types/interrupt'),
        ('Python 协程与任务', 'https://docs.python.org/3/library/asyncio-task.html'),
        ('Python 线程与 GIL', 'https://docs.python.org/3/library/threading.html'),
        ('PostgreSQL EXPLAIN', 'https://www.postgresql.org/docs/current/using-explain.html'),
        ('Java Thread.sleep', 'https://docs.oracle.com/javase/8/docs/api/java/lang/Thread.html'),
        ('MCP 架构与能力协商（2025-06-18 版）', 'https://modelcontextprotocol.io/specification/2025-06-18/architecture'),
    ]
    for label,url in links:
        story.append(P(f'<link href="{html.escape(url)}" color="#0B66A3">{html.escape(label)}</link>', 'body', raw=True))
    story += [P('外部资料只用于核对相关基础概念，不代表轻记已接入相应数据库、Java 服务或全部框架能力。项目现状以本次源码为准。', 'small'),
              P('个人事实与商业指标', 'h2'),
              P('职级、薪酬、晋升、团队人数、原公司经历、付费客户、真实用户量、AI Coding 比例和求职进展均未由工程证实。面试使用前请本人补充；没有的数据可以直接说明尚未统计。任何改进百分比都应有同口径基线和样本，不用功能数量代替效果。')]
    return story, '\n'.join(md)


def verify_algorithms():
    namespace = {}
    for letter,_,code,_ in APPENDICES:
        if letter not in ('C','H','J'):
            ast.parse(textwrap.dedent(code))
            exec(compile(textwrap.dedent(code), '<appendix>', 'exec'), namespace)
    assert namespace['two_sum']([2,7,11,15],9)==[0,1]
    assert namespace['two_sum']([3,3],6)==[0,1]
    a=[1,2,3,0,0,0]
    namespace['merge_sorted'](a,3,[2,5,6],3)
    assert a==[1,2,2,3,5,6]
    assert namespace['execute_weather'](namespace['weather_plan'](),lambda city: 20 if city=='Shanghai' else 15)['d']==5
    result=namespace['rearrange']([1,1,1,2,2,3])
    assert collections.Counter(result)==collections.Counter([1,1,1,2,2,3])
    assert all(a!=b for a,b in zip(result,result[1:]))
    assert namespace['rearrange']([1,1,1,2]) is None
    assert namespace['level_order'](None)==[]
    cache=namespace['LRUCache'](2)
    cache.put(1,1); cache.put(2,2)
    assert cache.get(1)==1
    cache.put(3,3)
    assert cache.get(2)==-1
    # Windows sandbox blocks the local socketpair required by asyncio's loop.
    # Validate coroutine structure without opening sockets during PDF creation.
    import inspect
    assert inspect.iscoroutinefunction(namespace['main'])


def main():
    verify_algorithms()
    rows=extract()
    DATA.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    story,markdown=make_story(rows)
    MD.write_text(markdown,encoding='utf-8')
    doc=InterviewDoc(PDF)
    doc.multiBuild(story)
    result=PdfReader(str(PDF))
    alltext='\n'.join(page.extract_text() or '' for page in result.pages)
    gids=re.findall(r'Q(\d{3})',alltext)
    assert len(gids)==642, len(gids)
    assert set(gids)=={f'{i:03}' for i in range(1,643)}
    assert len(doc.page_map)==642
    assert 'sk-' not in alltext
    assert all(row['question'] and len(row['text'])>=70 for row in rows)
    summary=dict(
        source=str(SOURCE),output=str(PDF),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        output_sha256=hashlib.sha256(PDF.read_bytes()).hexdigest(),question_count=642,
        distinct_answer_count=len({row['text'] for row in rows}),
        page_count=len(result.pages),company_count=len({row['company'] for row in rows}),
        round_count=len({(row['company'],row['round']) for row in rows}),
        questions_by_company=dict(collections.Counter(row['company'] for row in rows)),
        question_pages=doc.page_map,verified_at=datetime.now().isoformat(),
    )
    MANIFEST.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='question_pages'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
