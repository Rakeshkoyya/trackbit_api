"""Task attachments — notes + photos (plan P4-BE-02, Pro feature R6).

Each addition appends to the append-only chain ('commented' for notes,
'attached' for photos) so they show up in task history, and the row lives in the
attachments table for rendering. Storage rides the adapter (R2 or local).
"""

import uuid

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import plans
from app.core.context import CurrentMember
from app.core.exceptions import ValidationError
from app.core.visibility import can_view_board
from app.models import Attachment, Board, TaskInstance, User
from app.schemas.task import AttachmentOut
from app.services import events, storage

_MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_IMAGE = ("image/jpeg", "image/png", "image/webp", "image/gif")


class AttachmentService:
    def __init__(self, db: Session):
        self.db = db

    def _load_task(self, member: CurrentMember, instance_id: uuid.UUID) -> TaskInstance:
        inst = self.db.get(TaskInstance, instance_id)
        if inst is None:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Task")
        board = self.db.get(Board, inst.board_id)
        if board is None or not can_view_board(self.db, board=board, user_id=member.user_id):
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Task")
        return inst

    def _serialize(self, a: Attachment, names: dict) -> AttachmentOut:
        return AttachmentOut(
            id=a.id, kind=a.kind, content=a.content, file_url=a.file_url,
            uploaded_by_name=names.get(a.uploaded_by, "—"), created_at=a.created_at,
        )

    def list_for_task(self, member: CurrentMember, instance_id: uuid.UUID) -> list[AttachmentOut]:
        self._load_task(member, instance_id)
        rows = list(
            self.db.scalars(
                select(Attachment).where(Attachment.instance_id == instance_id)
                .order_by(Attachment.created_at)
            )
        )
        names = events.resolve_user_names(self.db, {a.uploaded_by for a in rows})
        return [self._serialize(a, names) for a in rows]

    def add_note(self, member: CurrentMember, instance_id: uuid.UUID, content: str) -> AttachmentOut:
        inst = self._load_task(member, instance_id)
        plans.enforce_attachments_allowed(member.org)
        a = Attachment(instance_id=inst.id, uploaded_by=member.user_id, kind="note", content=content)
        self.db.add(a)
        self.db.flush()
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="commented", actor_id=member.user_id)
        names = {member.user_id: self.db.get(User, member.user_id).name}
        return self._serialize(a, names)

    def add_photo(self, member: CurrentMember, instance_id: uuid.UUID, file: UploadFile) -> AttachmentOut:
        inst = self._load_task(member, instance_id)
        plans.enforce_attachments_allowed(member.org)
        content_type = file.content_type or "application/octet-stream"
        if content_type not in _ALLOWED_IMAGE:
            raise ValidationError("Only JPEG, PNG, WebP, or GIF images are supported.",
                                  code="bad_image_type")
        data = file.file.read()
        if len(data) > _MAX_PHOTO_BYTES:
            raise ValidationError("That image is larger than 10 MB.", code="image_too_large")
        if not data:
            raise ValidationError("Empty file.", code="empty_file")

        data = storage.maybe_downscale(data, content_type)
        key = storage.make_key(org_id=member.org_id, instance_id=inst.id, filename=file.filename or "photo")
        url = storage.save_bytes(key, data, content_type)

        a = Attachment(instance_id=inst.id, uploaded_by=member.user_id, kind="photo", file_url=url)
        self.db.add(a)
        self.db.flush()
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="attached", actor_id=member.user_id)
        names = {member.user_id: self.db.get(User, member.user_id).name}
        return self._serialize(a, names)
