"""
Configuration management for the trading platform.
"""
import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv
import pytz

# Load environment variables
load_dotenv()

class Config:
    """Central configuration class."""
    
    # API Keys
    POLYGON_API_KEY = os.getenv('POLYGON_API_KEY', '')
    ALPHA_VANTAGE_API_KEY = os.getenv('ALPHA_VANTAGE_API_KEY', '')
    NEWS_API_KEY = os.getenv('NEWS_API_KEY', '')
    
    # Database
    DATABASE_URL = os.getenv(
        'DATABASE_URL',
        'postgresql://trading_user:trading_pass@localhost:5432/trading_db'
    )
    
    # Redis
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
    REDIS_DB = int(os.getenv('REDIS_DB', 0))
    
    # Trading Configuration
    MARKET_OPEN_TIME = os.getenv('MARKET_OPEN_TIME', '09:30:00')
    MARKET_CLOSE_TIME = os.getenv('MARKET_CLOSE_TIME', '16:00:00')
    TRADING_WINDOW_MINUTES = int(os.getenv('TRADING_WINDOW_MINUTES', 10))
    TIMEZONE = pytz.timezone(os.getenv('TIMEZONE', 'America/New_York'))
    
    # Watchlist
    WATCHLIST: List[str] = os.getenv(
        'WATCHLIST',
        'AAPL,MSFT,GOOGL,AMZN,TSLA,NVDA,META,JPM,V,WMT'
    ).split(',')
    
    # Risk Parameters
    MAX_POSITION_SIZE = float(os.getenv('MAX_POSITION_SIZE', 10000))
    MAX_PORTFOLIO_EXPOSURE = float(os.getenv('MAX_PORTFOLIO_EXPOSURE', 0.3))
    STOP_LOSS_PCT = float(os.getenv('STOP_LOSS_PCT', 0.02))
    TAKE_PROFIT_PCT = float(os.getenv('TAKE_PROFIT_PCT', 0.03))
    
    # Technical Indicators Thresholds
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    MACD_SIGNAL_THRESHOLD = 0.0
    BOLLINGER_BAND_WIDTH = 2.0
    
    # Anomaly Detection
    ZSCORE_THRESHOLD = 3.0
    VOLATILITY_WINDOW = 20
    ANOMALY_SENSITIVITY = 0.05
    
    # Sentiment Analysis
    SENTIMENT_POSITIVE_THRESHOLD = 0.3
    SENTIMENT_NEGATIVE_THRESHOLD = -0.3
    
    # Decision Thresholds
    MIN_CONFIDENCE_THRESHOLD = 0.65
    SIGNAL_CONSENSUS_WEIGHT = {
        'technical': 0.35,
        'sentiment': 0.20,
        'anomaly': 0.20,
        'risk': 0.25
    }
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'logs/platform.log')
    
    # Dashboard
    DASHBOARD_PORT = int(os.getenv('DASHBOARD_PORT', 8501))
    DASHBOARD_REFRESH_SECONDS = int(os.getenv('DASHBOARD_REFRESH_SECONDS', 5))
    
    # Data Retention
    MARKET_DATA_RETENTION_DAYS = 90
    SIGNALS_RETENTION_DAYS = 30
    
    # Event Bus
    EVENT_STREAM_MAXLEN = 10000
    
    # Paths
    BASE_DIR = Path(__file__).parent.parent
    LOGS_DIR = BASE_DIR / 'logs'
    DATA_DIR = BASE_DIR / 'data'
    
    @classmethod
    def ensure_directories(cls):
        """Create necessary directories."""
        cls.LOGS_DIR.mkdir(exist_ok=True)
        cls.DATA_DIR.mkdir(exist_ok=True)
    
    @classmethod
    def validate(cls):
        """Validate critical configuration."""
        errors = []
        
        if not cls.POLYGON_API_KEY and not cls.ALPHA_VANTAGE_API_KEY:
            errors.append("At least one market data API key required")
        
        if not cls.DATABASE_URL:
            errors.append("DATABASE_URL is required")
        
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")
        
        return True


# Initialize
Config.ensure_directories()
