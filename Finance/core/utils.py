"""
Utility functions for the trading platform.
"""
from datetime import datetime, time, timedelta
from typing import Optional
import pytz
from loguru import logger
from core.config import Config


def is_market_open(timestamp: Optional[datetime] = None) -> bool:
    """
    Check if the market is currently open.
    
    Args:
        timestamp: Time to check (defaults to now)
        
    Returns:
        True if market is open
    """
    if timestamp is None:
        timestamp = datetime.now(Config.TIMEZONE)
    
    # Check if it's a weekday
    if timestamp.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False
    
    # Parse market hours
    market_open = time.fromisoformat(Config.MARKET_OPEN_TIME)
    market_close = time.fromisoformat(Config.MARKET_CLOSE_TIME)
    
    current_time = timestamp.time()
    
    return market_open <= current_time <= market_close


def is_in_trading_window(timestamp: Optional[datetime] = None) -> bool:
    """
    Check if we're in the first N minutes of trading (opening window).
    
    Args:
        timestamp: Time to check (defaults to now)
        
    Returns:
        True if in trading window
    """
    if timestamp is None:
        timestamp = datetime.now(Config.TIMEZONE)
    
    if not is_market_open(timestamp):
        return False
    
    # Parse market open time
    market_open = time.fromisoformat(Config.MARKET_OPEN_TIME)
    
    # Calculate trading window end time
    market_open_dt = datetime.combine(timestamp.date(), market_open)
    market_open_dt = Config.TIMEZONE.localize(market_open_dt)
    
    trading_window_end = market_open_dt + timedelta(
        minutes=Config.TRADING_WINDOW_MINUTES
    )
    
    return market_open_dt <= timestamp <= trading_window_end


def get_next_market_open(timestamp: Optional[datetime] = None) -> datetime:
    """
    Get the next market open time.
    
    Args:
        timestamp: Starting time (defaults to now)
        
    Returns:
        Next market open datetime
    """
    if timestamp is None:
        timestamp = datetime.now(Config.TIMEZONE)
    
    market_open = time.fromisoformat(Config.MARKET_OPEN_TIME)
    
    # Start with today
    next_open = datetime.combine(timestamp.date(), market_open)
    next_open = Config.TIMEZONE.localize(next_open)
    
    # If we're past today's open, move to tomorrow
    if timestamp >= next_open:
        next_open += timedelta(days=1)
    
    # Skip weekends
    while next_open.weekday() >= 5:
        next_open += timedelta(days=1)
    
    return next_open


def get_time_until_market_open(timestamp: Optional[datetime] = None) -> timedelta:
    """
    Get time remaining until next market open.
    
    Args:
        timestamp: Starting time (defaults to now)
        
    Returns:
        Time delta until market open
    """
    if timestamp is None:
        timestamp = datetime.now(Config.TIMEZONE)
    
    next_open = get_next_market_open(timestamp)
    return next_open - timestamp


def calculate_position_size(
    price: float,
    max_position_value: float,
    confidence: float
) -> int:
    """
    Calculate position size based on confidence and risk parameters.
    
    Args:
        price: Stock price
        max_position_value: Maximum position value in dollars
        confidence: Confidence score (0-1)
        
    Returns:
        Number of shares to trade
    """
    # Scale position by confidence
    position_value = max_position_value * confidence
    
    # Calculate shares
    shares = int(position_value / price)
    
    return max(1, shares)  # At least 1 share


def calculate_stop_loss(entry_price: float, action: str) -> float:
    """
    Calculate stop loss price.
    
    Args:
        entry_price: Entry price
        action: "BUY" or "SELL"
        
    Returns:
        Stop loss price
    """
    if action == "BUY":
        return entry_price * (1 - Config.STOP_LOSS_PCT)
    else:
        return entry_price * (1 + Config.STOP_LOSS_PCT)


def calculate_take_profit(entry_price: float, action: str) -> float:
    """
    Calculate take profit price.
    
    Args:
        entry_price: Entry price
        action: "BUY" or "SELL"
        
    Returns:
        Take profit price
    """
    if action == "BUY":
        return entry_price * (1 + Config.TAKE_PROFIT_PCT)
    else:
        return entry_price * (1 - Config.TAKE_PROFIT_PCT)


def format_currency(value: float) -> str:
    """Format value as currency."""
    return f"${value:,.2f}"


def format_percentage(value: float) -> str:
    """Format value as percentage."""
    return f"{value * 100:.2f}%"


def calculate_sharpe_ratio(
    returns: list,
    risk_free_rate: float = 0.02
) -> float:
    """
    Calculate Sharpe ratio.
    
    Args:
        returns: List of returns
        risk_free_rate: Annual risk-free rate
        
    Returns:
        Sharpe ratio
    """
    import numpy as np
    
    if len(returns) < 2:
        return 0.0
    
    returns_array = np.array(returns)
    excess_returns = returns_array - (risk_free_rate / 252)  # Daily risk-free rate
    
    if np.std(excess_returns) == 0:
        return 0.0
    
    return np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)


def setup_logging():
    """Configure logging for the platform."""
    from pathlib import Path
    
    # Remove default logger
    logger.remove()
    
    # Add console logger
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=Config.LOG_LEVEL,
        colorize=True
    )
    
    # Add file logger
    log_file = Path(Config.LOG_FILE)
    log_file.parent.mkdir(exist_ok=True)
    
    logger.add(
        Config.LOG_FILE,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} - {message}",
        level=Config.LOG_LEVEL,
        rotation="1 day",
        retention="30 days",
        compression="zip"
    )
    
    logger.info("Logging initialized")
