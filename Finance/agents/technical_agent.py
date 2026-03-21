"""
Technical Analysis Agent - Computes technical indicators.
RSI, MACD, Moving Averages, Bollinger Bands, etc.
"""
import time
from datetime import datetime
from collections import defaultdict, deque
from typing import Dict, Any, Optional
import numpy as np
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from core.schemas import Action
from data.db import db_manager


class TechnicalAgent:
    """
    Computes technical indicators and generates trading signals.
    Subscribes to: market_data
    Publishes: technical_signals
    """
    
    def __init__(self):
        self.name = "TechnicalAgent"
        self.price_history = defaultdict(lambda: deque(maxlen=200))
        self.volume_history = defaultdict(lambda: deque(maxlen=200))
        
        logger.info(f"{self.name} initialized")
    
    def run(self):
        """Main agent loop - subscribe to market data events."""
        logger.info(f"{self.name} starting...")
        
        event_bus.subscribe(
            EventType.MARKET_DATA,
            f"{self.name}_consumer",
            self.process_market_data
        )
    
    def process_market_data(self, data: Dict[str, Any]):
        """Process incoming market data and compute indicators."""
        try:
            symbol = data['symbol']
            price = data['close']
            volume = data['volume']
            timestamp = data.get('timestamp', datetime.now(Config.TIMEZONE))
            
            # Update history
            self.price_history[symbol].append(price)
            self.volume_history[symbol].append(volume)
            
            # Need at least 50 data points for meaningful indicators
            if len(self.price_history[symbol]) < 50:
                logger.debug(f"Building history for {symbol}: {len(self.price_history[symbol])}/50")
                return
            
            # Compute indicators
            indicators = self._compute_indicators(symbol)
            
            if indicators:
                # Add metadata
                indicators['symbol'] = symbol
                indicators['timestamp'] = timestamp
                
                # Generate signal interpretation
                indicators['signal'] = self._interpret_signals(indicators)
                
                # Publish to event bus
                event_bus.publish_technical_signals(symbol, indicators)
                
                # Store in database
                db_data = {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'signal_type': 'technical',
                    **indicators
                }
                db_manager.insert_signal(db_data)
                
                logger.info(
                    f"📈 {symbol} Technical: RSI={indicators.get('rsi', 0):.1f}, "
                    f"MACD={indicators.get('macd', 0):.3f}, Signal={indicators['signal']}"
                )
                
        except Exception as e:
            logger.error(f"Error processing market data: {e}")
    
    def _compute_indicators(self, symbol: str) -> Dict[str, Any]:
        """Compute all technical indicators for a symbol."""
        prices = np.array(self.price_history[symbol])
        volumes = np.array(self.volume_history[symbol])
        
        indicators = {}
        
        try:
            # RSI (Relative Strength Index)
            indicators['rsi'] = self._calculate_rsi(prices, period=14)
            
            # MACD (Moving Average Convergence Divergence)
            macd_data = self._calculate_macd(prices)
            indicators.update(macd_data)
            
            # Moving Averages
            indicators['sma_20'] = self._calculate_sma(prices, 20)
            indicators['sma_50'] = self._calculate_sma(prices, 50)
            indicators['ema_12'] = self._calculate_ema(prices, 12)
            indicators['ema_26'] = self._calculate_ema(prices, 26)
            
            # Bollinger Bands
            bb_data = self._calculate_bollinger_bands(prices, period=20, std_dev=2)
            indicators.update(bb_data)
            
            # Volume Analysis
            indicators['volume_sma'] = self._calculate_sma(volumes, 20)
            
            return indicators
            
        except Exception as e:
            logger.error(f"Error computing indicators for {symbol}: {e}")
            return {}
    
    def _calculate_rsi(self, prices: np.ndarray, period: int = 14) -> float:
        """Calculate Relative Strength Index."""
        if len(prices) < period + 1:
            return 50.0
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)
    
    def _calculate_macd(
        self,
        prices: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> Dict[str, float]:
        """Calculate MACD indicator."""
        if len(prices) < slow:
            return {'macd': 0.0, 'macd_signal': 0.0, 'macd_hist': 0.0}
        
        ema_fast = self._calculate_ema(prices, fast)
        ema_slow = self._calculate_ema(prices, slow)
        
        macd = ema_fast - ema_slow
        
        # Calculate signal line (EMA of MACD)
        # For simplicity, using SMA here
        macd_signal = macd  # Simplified
        macd_hist = macd - macd_signal
        
        return {
            'macd': float(macd),
            'macd_signal': float(macd_signal),
            'macd_hist': float(macd_hist)
        }
    
    def _calculate_sma(self, values: np.ndarray, period: int) -> float:
        """Calculate Simple Moving Average."""
        if len(values) < period:
            return float(np.mean(values))
        
        return float(np.mean(values[-period:]))
    
    def _calculate_ema(self, values: np.ndarray, period: int) -> float:
        """Calculate Exponential Moving Average."""
        if len(values) < period:
            return float(np.mean(values))
        
        multiplier = 2 / (period + 1)
        ema = values[0]
        
        for value in values[1:]:
            ema = (value * multiplier) + (ema * (1 - multiplier))
        
        return float(ema)
    
    def _calculate_bollinger_bands(
        self,
        prices: np.ndarray,
        period: int = 20,
        std_dev: float = 2.0
    ) -> Dict[str, float]:
        """Calculate Bollinger Bands."""
        if len(prices) < period:
            mean = np.mean(prices)
            std = 0.0
        else:
            recent_prices = prices[-period:]
            mean = np.mean(recent_prices)
            std = np.std(recent_prices)
        
        upper = mean + (std_dev * std)
        lower = mean - (std_dev * std)
        
        return {
            'bollinger_upper': float(upper),
            'bollinger_middle': float(mean),
            'bollinger_lower': float(lower)
        }
    
    def _interpret_signals(self, indicators: Dict[str, Any]) -> str:
        """
        Interpret technical indicators to generate a signal.
        Returns: 'bullish', 'bearish', or 'neutral'
        """
        bullish_count = 0
        bearish_count = 0
        
        # RSI Analysis
        rsi = indicators.get('rsi', 50)
        if rsi < Config.RSI_OVERSOLD:
            bullish_count += 2  # Strong buy signal
        elif rsi < 40:
            bullish_count += 1
        elif rsi > Config.RSI_OVERBOUGHT:
            bearish_count += 2  # Strong sell signal
        elif rsi > 60:
            bearish_count += 1
        
        # MACD Analysis
        macd = indicators.get('macd', 0)
        macd_signal = indicators.get('macd_signal', 0)
        if macd > macd_signal and macd > 0:
            bullish_count += 1
        elif macd < macd_signal and macd < 0:
            bearish_count += 1
        
        # Moving Average Analysis
        sma_20 = indicators.get('sma_20', 0)
        sma_50 = indicators.get('sma_50', 0)
        if sma_20 > sma_50:
            bullish_count += 1
        elif sma_20 < sma_50:
            bearish_count += 1
        
        # Bollinger Bands Analysis
        # Current price compared to bands (using most recent price)
        # Note: Would need current price passed in for accurate analysis
        
        # Determine overall signal
        if bullish_count > bearish_count + 1:
            return 'bullish'
        elif bearish_count > bullish_count + 1:
            return 'bearish'
        else:
            return 'neutral'


def main():
    """Run the Technical Analysis Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = TechnicalAgent()
    agent.run()


if __name__ == "__main__":
    main()
