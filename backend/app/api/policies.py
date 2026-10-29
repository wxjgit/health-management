"""Versioned notices; changing operator or model provider invalidates old grants."""
import hashlib
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Request


def policy(settings):
    provider = urlsplit(settings.model_base_url).hostname or ''
    content = {
        'revision': '2026-10-29.1', 'operator': settings.privacy_operator,
        'contact': settings.privacy_contact, 'backup_retention_days': settings.backup_retention_days,
        'model_provider': provider, 'model_name': settings.model_name,
        'model_available': settings.mode == 'model' and not settings.local_only,
        'privacy': [
            '微信登录仅用于建立账号，不读取头像、昵称或手机号。',
            '你填写的档案、饮食、体重、睡眠、运动、生理周期、聊天及计划保存在服务端，按账号隔离，用于记录、统计、追问和复盘。健康信息由你自主提供。',
            '服务保存必要的登录凭证摘要、授权版本和时间、请求次数及运行日志；运行日志不记录健康原文或模型提示词。',
            '你可以删除记录、导出本人数据、撤回模型授权或注销账号。注销立即撤销所有登录凭证，并等待运行中的操作结束后清理服务端数据。',
            '注销后仅保留内部随机账号 ID 和注销时间组成的最小清单，用于阻止备份恢复重新引入已注销数据，不保留该账号的 OpenID 或健康原文。',
            f'备份最长保留 {settings.backup_retention_days} 天；恢复时必须使用最新注销清单剔除已注销账号。已自行分享的文件需要自行删除。',
        ],
        'model_notice': [
            f'可选模型服务：{provider or "未配置"}；模型：{settings.model_name or "未配置"}。',
            '仅在你另行同意后，才可将本次输入、必要的档案和相关健康记录片段发送给上述供应商，用于安全判断、输入理解、营养粗估和建议表达。不会默认发送完整历史。',
            '同意不是使用基础记录功能的条件；拒绝或撤回后使用服务器本地规则，识别能力和建议功能会受限。本地规则仍在服务端执行，并非数据只留在手机。',
            '模型可能出错，营养估算仅供记录参考。本服务不提供诊断、治疗或紧急医疗服务；外部供应商对已接收数据的处理还受其服务条款约束。',
        ],
        'terms': [
            '请仅提交你有权提供的数据，并核对日期、分量及数值后确认重要操作。',
            '本服务用于个人健康记录与一般生活方式复盘，不替代专业医疗判断。异常情况请寻求适当的专业帮助。',
            '你可通过上述联系渠道咨询数据处理、反馈问题或提出权利请求。',
        ],
    }
    version = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {**content, 'version': version,
            'ready': bool(settings.privacy_operator.strip() and settings.privacy_contact.strip())}


router = APIRouter(prefix='/api', tags=['policies'])


@router.get('/policies')
def policies(request: Request):
    return policy(request.app.state.settings)
