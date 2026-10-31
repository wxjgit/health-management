"""提供隐私删除预览、执行、查询和重试接口。"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..privacy.deletion import DeletionError
from .deps import get_engine, managed_api


router = APIRouter(prefix='/api/deletions', tags=['privacy'])


class PreviewRequest(BaseModel):
    target_type: Literal['event', 'fact']
    target_id: str = Field(min_length=1, max_length=160)


class DeleteRequest(BaseModel):
    preview_id: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=160)


@router.post('/preview')
def preview(body: PreviewRequest, engine=Depends(get_engine)):
    return engine.deletions.preview(body.target_type, body.target_id)


@router.post('')
@managed_api
def execute(body: DeleteRequest, engine=Depends(get_engine)):
    with engine.lock:
        row = engine.repo.deletion_preview(body.preview_id)
        if not row:
            raise DeletionError('not_found', '删除预览不存在，请重新查看范围。', 404)
        return engine.deletions.delete(row['target_type'], row['target_id'], preview_id=body.preview_id,
                                       request_id=body.request_id, require_preview=True)


@router.get('/{deletion_id}')
def status(deletion_id: str, engine=Depends(get_engine)):
    return engine.deletions.status(deletion_id)


@router.post('/{deletion_id}/retry')
def retry(deletion_id: str, engine=Depends(get_engine)):
    return engine.deletions.status(deletion_id, retry=True)
