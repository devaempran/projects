"""
Anomaly Detection Agent - Detects unusual market behavior.
Uses statistical methods and machine learning.
"""
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, Any
import numpy as np
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from data.db import db_manager


class AnomalyAgent:
    """
    Detects anomalies in market data using statistical methods.
    Subscribes to: market_data
    Publishes: anomalies
    """
    
    def __init__(self):
        self.name = "AnomalyAgent"
        self.price_history = defaultdict(lambda: deque(maxlen=100))
        self.volume_history = defaultdict(lambda: deque(maxlen=100))
        
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
        """Process market data and detect anomalies."""
        try:
            symbol = data['symbol']
            price = data['close']
            volume = data['volume']
            timestamp = data.get('timestamp', datetime.now(Config.TIMEZONE))
            
            # Update history
            self.price_history[symbol].append(price)
            self.volume_history[symbol].append(volume)
            
            # Need sufficient data for analysis
            if len(self.price_history[symbol]) < 30:
                return
            
            # Detect anomalies
            anomalies = []
            
            # Price Z-score anomaly
            price_anomaly = self._detect_zscore_anomaly(
                price,
                self.price_history[symbol],
                'price'
            )
            if price_anomaly:
                anomalies.append(price_anomaly)
            
            # Volume anomaly
            volume_anomaly = self._detect_zscore_anomaly(
                volume,
                self.volume_history[symbol],
                'volume'
            )
            if volume_anomaly:
                anomalies.append(volume_anomaly)
            
            # Volatility anomaly
            volatility_anomaly = self._detect_volatility_anomaly(symbol, price)
            if volatility_anomaly:
                anomalies.append(volatility_anomaly)
            
            # Price spike detection
            spike_anomaly = self._detect_price_spike(symbol, price)
            if spike_anomaly:
                anomalies.append(spike_anomaly)
            
            # Publish anomalies
            for anomaly in anomalies:
                anomaly_data = {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'anomaly_type': anomaly['type'],
                    'score': anomaly['score'],
                    'severity': anomaly['severity'],
                    'details': anomaly['details'],
                    'baseline_value': anomaly.get('baseline'),
                    'current_value': anomaly.get('current')
                }
                
                event_bus.publish_anomaly(symbol, anomaly_data)
                
                # Store in database
                db_data = {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'signal_type': 'anomaly',
                    'anomaly_score': anomaly['score'],
                    'anomaly_type': anomaly['type']
                }
                db_manager.insert_signal(db_data)
                
                logger.warning(
                    f"⚠️  {symbol} Anomaly: {anomaly['type']} "
                    f"(severity: {anomaly['severity']}, score: {anomaly['score']:.2f})"
                )
                
        except Exception as e:
            logger.error(f"Error detecting anomalies: {e}")
    
    def _detect_zscore_anomaly(
        self,
        current_value: float,
        history: deque,
        value_type: str
    ) -> Dict[str, Any]:
        """Detect anomaly using Z-score method."""
        try:
            values = np.array(history)
            mean = np.mean(values)
            std = np.std(values)
            
            if std == 0:
                return None
            
            z_score = abs((current_value - mean) / std)
            
            if z_score > Config.ZSCORE_THRESHOLD:
                severity = 'high' if z_score > 4 else 'medium'
                
                return {
                    'type': 'zscore',
                    'score': float(z_score),
                    'severity': severity,
                    'details': f'{value_type.capitalize()} Z-score anomaly detected',
                    'baseline': float(mean),
                    'current': float(current_value)
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Z-score calculation error: {e}")
            return None
    
    def _detect_volatility_anomaly(
        self,
        symbol: str,
        current_price: float
    ) -> Dict[str, Any]:
        """Detect abnormal volatility."""
        try:
            prices = np.array(self.price_history[symbol])
            
            if len(prices) < Config.VOLATILITY_WINDOW:
                return None
            
            # Calculate rolling volatility
            returns = np.diff(prices) / prices[:-1]
            recent_volatility = np.std(returns[-Config.VOLATILITY_WINDOW:])
            historical_volatility = np.std(returns)
            
            if historical_volatility == 0:
                return None
            
            volatility_ratio = recent_volatility / historical_volatility
            
            # Check if volatility is abnormally high
            if volatility_ratio > 2.0:
                severity = 'high' if volatility_ratio > 3.0 else 'medium'
                
                return {
                    'type': 'volatility',
                    'score': float(volatility_ratio),
                    'severity': severity,
                    'details': f'Elevated volatility detected ({volatility_ratio:.2f}x normal)',
                    'baseline': float(historical_volatility),
                    'current': float(recent_volatility)
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Volatility calculation error: {e}")
            return None
    
    def _detect_price_spike(
        self,
        symbol: str,
        current_price: float
    ) -> Dict[str, Any]:
        """Detect sudden price spikes."""
        try:
            prices = list(self.price_history[symbol])
            
            if len(prices) < 5:
                return None
            
            # Check recent price change
            previous_price = prices[-2] if len(prices) > 1 else prices[-1]
            price_change_pct = abs((current_price - previous_price) / previous_price)
            
            # Alert on significant spikes (>2% in one period)
            if price_change_pct > 0.02:
                severity = 'high' if price_change_pct > 0.05 else 'medium'
                
                return {
                    'type': 'price_spike',
                    'score': float(price_change_pct * 100),
                    'severity': severity,
                    'details': f'Price spike of {price_change_pct*100:.2f}% detected',
                    'baseline': float(previous_price),
                    'current': float(current_price)
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Price spike detection error: {e}")
            return None
    
    def _detect_isolation_forest_anomaly(
        self,
        symbol: str,
        features: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Advanced anomaly detection using Isolation Forest.
        Optional - requires scikit-learn.
        """
        try:
            from sklearn.ensemble import IsolationForest
            
            # This is a placeholder for more advanced ML-based detection
            # Would require feature engineering and model training
            
            return None
            
        except ImportError:
            return None
        except Exception as e:
            logger.error(f"Isolation Forest error: {e}")
            return None


def main():
    """Run the Anomaly Detection Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = AnomalyAgent()
    agent.run()


if __name__ == "__main__":
    main()
