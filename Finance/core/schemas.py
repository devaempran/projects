"""
Event schemas and data models for the trading platform.
"""
from datetime import datetime
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from enum import Enum


class EventType(str, Enum):
    """Event type enumeration."""
    MARKET_DATA = "market_data"
    TECHNICAL_SIGNALS = "technical_signals"
    SENTIMENT_SIGNALS = "sentiment_signals"
    ANOMALIES = "anomalies"
    RISK_ASSESSMENTS = "risk_assessments"
    DEBATE_OUTCOME = "debate_outcome"
    DECISIONS = "decisions"
    TRADES = "trades"
    ALERTS = "alerts"


class Action(str, Enum):
    """Trading action enumeration."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# Event Schemas

class MarketDataEvent(BaseModel):
    """Market data event schema."""
    symbol: str
    timestamp: datetime
    price: float
    volume: int
    open: float
    high: float
    low: float
    close: float
    vwap: Optional[float] = None
    trade_count: Optional[int] = None


class TechnicalSignalsEvent(BaseModel):
    """Technical analysis signals schema."""
    symbol: str
    timestamp: datetime
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_middle: Optional[float] = None
    bollinger_lower: Optional[float] = None
    volume_sma: Optional[float] = None
    signal: Optional[str] = None  # "bullish", "bearish", "neutral"


class SentimentSignalsEvent(BaseModel):
    """Sentiment analysis signals schema."""
    symbol: str
    timestamp: datetime
    sentiment_score: float  # -1 to 1
    magnitude: float  # 0 to 1
    source: str
    news_count: int = 0
    headline: Optional[str] = None


class AnomalyEvent(BaseModel):
    """Anomaly detection event schema."""
    symbol: str
    timestamp: datetime
    anomaly_type: str  # "zscore", "volatility", "volume", "price_spike"
    score: float
    severity: str  # "low", "medium", "high"
    details: str
    baseline_value: Optional[float] = None
    current_value: Optional[float] = None


class RiskAssessmentEvent(BaseModel):
    """Risk assessment event schema."""
    symbol: str
    timestamp: datetime
    exposure: float  # Current position exposure
    volatility: float  # Annualized volatility
    var_95: float  # Value at Risk (95% confidence)
    max_drawdown: float
    sharpe_ratio: Optional[float] = None
    position_size_recommendation: float
    risk_level: str  # "low", "medium", "high"


class DebateOutcomeEvent(BaseModel):
    """Debate agents outcome schema."""
    symbol: str
    timestamp: datetime
    bull_score: float  # 0-1
    bull_arguments: list[str]
    bear_score: float  # 0-1
    bear_arguments: list[str]
    judge_decision: Action
    judge_confidence: float  # 0-1
    judge_rationale: str


class DecisionEvent(BaseModel):
    """Trading decision event schema."""
    symbol: str
    timestamp: datetime
    action: Action
    confidence: float  # 0-1
    reason: str
    supporting_signals: Dict[str, Any]
    position_size: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class TradeEvent(BaseModel):
    """Trade execution event schema."""
    trade_id: str
    symbol: str
    timestamp: datetime
    action: Action
    quantity: int
    price: float
    commission: float = 0.0
    total_cost: float
    portfolio_value: float
    pnl: float = 0.0
    pnl_pct: float = 0.0


class AlertEvent(BaseModel):
    """Alert event schema."""
    timestamp: datetime
    alert_type: str
    severity: AlertSeverity
    symbol: Optional[str] = None
    message: str
    details: Optional[Dict[str, Any]] = None


# Database Models (SQLAlchemy will use these as reference)

class MarketDataRecord:
    """Market data database record."""
    __tablename__ = 'market_data'
    
    fields = [
        'id', 'symbol', 'timestamp', 'open', 'high', 'low', 'close',
        'volume', 'vwap', 'trade_count', 'created_at'
    ]


class SignalRecord:
    """Signal database record."""
    __tablename__ = 'signals'
    
    fields = [
        'id', 'symbol', 'timestamp', 'signal_type', 'rsi', 'macd',
        'macd_signal', 'sma_20', 'sma_50', 'ema_12', 'ema_26',
        'bollinger_upper', 'bollinger_lower', 'sentiment_score',
        'anomaly_score', 'created_at'
    ]


class DecisionRecord:
    """Decision database record."""
    __tablename__ = 'decisions'
    
    fields = [
        'id', 'symbol', 'timestamp', 'action', 'confidence', 'reason',
        'position_size', 'entry_price', 'stop_loss', 'take_profit',
        'created_at'
    ]


class TradeRecord:
    """Trade database record."""
    __tablename__ = 'trades'
    
    fields = [
        'id', 'trade_id', 'symbol', 'timestamp', 'action', 'quantity',
        'price', 'commission', 'total_cost', 'portfolio_value',
        'pnl', 'pnl_pct', 'created_at'
    ]


class AlertRecord:
    """Alert database record."""
    __tablename__ = 'alerts'
    
    fields = [
        'id', 'timestamp', 'alert_type', 'severity', 'symbol',
        'message', 'details', 'created_at'
    ]
