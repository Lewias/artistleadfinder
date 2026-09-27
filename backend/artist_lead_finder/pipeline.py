"""Application orchestration of independent analysis and persistence modules."""

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .analysis import ProfileAnalyzer
from .classification import RuleBasedClassifier
from .discovery import DiscoveryRecord
from .models import LeadAnalysis, LeadScoreBreakdown, SearchJob
from .normalization import normalize
from .repository import upsert_candidate
from .schemas import SearchConfiguration
from .scoring import LeadScorer


@dataclass(frozen=True)
class ProcessResult:
    analyzed: bool
    artist: bool
    qualified: bool


class CandidatePipeline:
    def __init__(self, sessions: sessionmaker[Session], scorer: LeadScorer | None = None) -> None:
        self.sessions = sessions
        self.analyzer = ProfileAnalyzer()
        self.classifier = RuleBasedClassifier()
        self.scorer = scorer or LeadScorer()

    def __call__(
        self, job_id: int, record: DiscoveryRecord, config: SearchConfiguration
    ) -> ProcessResult:
        candidate = normalize(record.candidate)
        signals = self.analyzer.analyze(candidate)
        classification = self.classifier.classify(signals)
        score = self.scorer.score(candidate, signals, classification, config)
        with self.sessions.begin() as session:
            # Upsert sources before deciding whether this job already analyzed the identity.
            lead, _created, already_in_job = upsert_candidate(session, job_id, record)
            existing_analysis = session.get(LeadAnalysis, lead.id)
            if already_in_job and existing_analysis:
                return ProcessResult(False, False, False)
            qualified_count = session.get(SearchJob, job_id).qualified_leads
            qualified = score.qualified and qualified_count < config.target_leads
            lead.artist_probability = classification.confidence
            lead.primary_genre = classification.primary_genre
            lead.genres = classification.genres
            lead.lead_score = score.score
            # Do not erase a manual CRM decision on rediscovery.
            if lead.status == "new":
                lead.status = "qualified" if qualified else "new"
            analysis = existing_analysis or LeadAnalysis(lead_id=lead.id)
            session.add(analysis)
            for key, value in classification.model_dump().items():
                setattr(analysis, key, value)
            analysis.extracted_signals = asdict(signals)
            for item in score.breakdown:
                breakdown = session.scalar(
                    select(LeadScoreBreakdown).where(
                        LeadScoreBreakdown.lead_id == lead.id, LeadScoreBreakdown.rule == item.rule
                    )
                )
                if breakdown is None:
                    breakdown = LeadScoreBreakdown(lead_id=lead.id, **item.model_dump())
                    session.add(breakdown)
                else:
                    breakdown.points = item.points
                    breakdown.reason = item.reason
            return ProcessResult(True, classification.is_artist, qualified)
