"""Auto-regulation package.

Legacy symbols (regulate, compute_regulation_signals, RegulationSignals, RecType)
remain importable from this package's root for backward compatibility with
existing callers (chat_prompts, narration, daily_goals, routes/api). They will
be retired in PR 5 of the session-brief sprint.

New code should import from .engine + .schemas directly.
"""

from .engine import compute_regulation
from .legacy import (
    RecType,
    RegulationSignals,
    compute_regulation_signals,
    regulate,
)
from .schemas import (
    DailySnapshot,
    Flag,
    HealthEventSnapshot,
    MissingInput,
    RegulationCall,
    RegulationState,
    SessionBrief,
    TrainingModifier,
    TrendSummary,
    WeightTrend,
    WorkoutSummary,
)

__all__ = [
    "RegulationSignals",
    "RecType",
    "regulate",
    "compute_regulation_signals",
    "compute_regulation",
    "RegulationState",
    "TrainingModifier",
    "RegulationCall",
    "DailySnapshot",
    "HealthEventSnapshot",
    "WorkoutSummary",
    "TrendSummary",
    "WeightTrend",
    "Flag",
    "MissingInput",
    "SessionBrief",
]
