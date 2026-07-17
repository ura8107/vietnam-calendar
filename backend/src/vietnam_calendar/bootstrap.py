import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update

from .config import get_settings
from .db import SessionFactory, engine
from .models import AuditLog, Session, User
from .security import is_valid_argon2id_hash


async def bootstrap_admin(*, rotate_password: bool = False) -> str:
    """Create once by default; rotate only under an explicit operator action."""
    settings = get_settings()
    if not is_valid_argon2id_hash(settings.admin_password_hash):
        raise RuntimeError("ADMIN_PASSWORD_HASH must contain a valid Argon2id encoded hash")
    async with SessionFactory() as db, db.begin():
        user = (await db.execute(select(User).where(User.username == settings.admin_username).with_for_update())).scalar_one_or_none()
        if user is None:
            user = User(username=settings.admin_username, password_hash=settings.admin_password_hash, is_admin=True)
            db.add(user); await db.flush()
            db.add(AuditLog(actor_user_id=user.id, action="admin.created", entity_type="user", entity_id=str(user.id), before_values=None, after_values={"username": user.username}, details={}))
            result = "created"
        elif rotate_password:
            before = {"password_rotated": False}; user.password_hash = settings.admin_password_hash
            await db.execute(update(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
            db.add(AuditLog(actor_user_id=user.id, action="admin.password_rotated", entity_type="user", entity_id=str(user.id), before_values=before, after_values={"password_rotated": True}, details={"sessions_revoked": True}))
            result = "rotated"
        else:
            result = "unchanged"
    await engine.dispose(); return result


async def prune_sessions() -> int:
    async with SessionFactory() as db, db.begin():
        result = await db.execute(delete(Session).where(or_(Session.expires_at <= datetime.now(UTC), Session.revoked_at.is_not(None))))
        count = result.rowcount or 0
    await engine.dispose(); return count


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--rotate-password", action="store_true"); parser.add_argument("--prune-sessions", action="store_true")
    args = parser.parse_args()
    if args.prune_sessions: print(asyncio.run(prune_sessions()))
    else: print(asyncio.run(bootstrap_admin(rotate_password=args.rotate_password)))

if __name__ == "__main__": main()
