"""FollowUpScheduler: plans a sequence's follow-ups after a successful initial message
and cancels them when the lead replies. Sending follow-ups belongs to the follow-up
module; this module only creates and cancels the planned jobs."""

from datetime import datetime, timedelta

from sqlalchemy import select, update

from ..models import FollowUpJob, FollowUpSequence, OutreachTemplate

MAX_STEPS = 5


def validate_steps(session, steps: object) -> list[dict]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"В цепочке должно быть от 1 до {MAX_STEPS} шагов.")
    result = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Некорректный шаг цепочки.")
        delay, template_id = step.get("delay_days"), step.get("template_id")
        if isinstance(delay, bool) or not isinstance(delay, int) or not 1 <= delay <= 60:
            raise ValueError("Задержка шага — от 1 до 60 дней.")
        if not isinstance(template_id, int) or session.get(OutreachTemplate, template_id) is None:
            raise ValueError("Шаблон шага не найден.")
        result.append({"delay_days": delay, "template_id": template_id})
    return result


class FollowUpScheduler:
    def start(
        self, session, *, conversation_id: int, sequence_id: int, sent_at: datetime
    ) -> list[FollowUpJob]:
        """Plan every step from `sent_at`; runs inside the send transaction. Steps already
        planned for this conversation are kept, never duplicated."""
        sequence = session.get(FollowUpSequence, sequence_id)
        if sequence is None or not sequence.enabled:
            return []
        planned = set(
            session.scalars(
                select(FollowUpJob.step_index).where(
                    FollowUpJob.conversation_id == conversation_id,
                    FollowUpJob.sequence_id == sequence_id,
                )
            )
        )
        jobs, at = [], sent_at
        for index, step in enumerate(sequence.steps):
            at = at + timedelta(days=step["delay_days"])
            if index in planned:
                continue
            job = FollowUpJob(
                conversation_id=conversation_id,
                sequence_id=sequence_id,
                step_index=index,
                template_id=step["template_id"],
                scheduled_at=at,
            )
            session.add(job)
            jobs.append(job)
        return jobs

    def cancel(self, session, conversation_id: int, reason: str = "REPLY_RECEIVED") -> int:
        result = session.execute(
            update(FollowUpJob)
            .where(FollowUpJob.conversation_id == conversation_id, FollowUpJob.status == "pending")
            .values(status="cancelled", cancel_reason=reason)
        )
        return result.rowcount or 0
