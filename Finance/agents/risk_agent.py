"""
Risk Management Agent - Assesses portfolio risk and provides recommendations.
"""
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, Any
import numpy as np
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from data.db import db_manager


class RiskAgent:
    """
    Manages risk assessment and position sizing.
    Subscribes to: market_data, trades
    Publishes: risk_assessments
    """
    
    def __init__(self):
        self.name = "RiskAgent"
        self.price_history = defaultdict(lambda: deque(maxlen=100))
        self.returns_history = defaultdict(lambda: deque(maxlen=100))
        self.positions = {}
        self.portfolio_value = 100000.0  # Starting capital
        
        logger.info(f"{self.name} initialized with ${self.portfolio_value:,.2f}")
    
    def run(self):
        """Main agent loop - subscribe to relevant events."""
        logger.info(f"{self.name} starting...")
        
        event_bus.subscribe(
            EventType.MARKET_DATA,
            f"{self.name}_consumer",
            self.process_market_data
        )
    
    def process_market_data(self, data: Dict[str, Any]):
        """Process market data and compute risk metrics."""
        try:
            symbol = data['symbol']
            price = data['close']
            timestamp = data.get('timestamp', datetime.now(Config.TIMEZONE))
            
            # Update price history
            self.price_history[symbol].append(price)
            
            # Calculate returns
            if len(self.price_history[symbol]) >= 2:
                prev_price = list(self.price_history[symbol])[-2]
                ret = (price - prev_price) / prev_price
                self.returns_history[symbol].append(ret)
            
            # Need sufficient data
            if len(self.price_history[symbol]) < 20:
                return
            
            # Compute risk assessment
            assessment = self._compute_risk_assessment(symbol, price)
            
            if assessment:
                assessment['symbol'] = symbol
                assessment['timestamp'] = timestamp
                
                # Publish to event bus
                event_bus.publish_risk_assessment(symbol, assessment)
                
                logger.info(
                    f"⚖️  {symbol} Risk: Volatility={assessment['volatility']:.2%}, "
                    f"VaR={assessment['var_95']:.2f}, Level={assessment['risk_level']}"
                )
                
        except Exception as e:
            logger.error(f"Error in risk assessment: {e}")
    
    def _compute_risk_assessment(
        self,
        symbol: str,
        current_price: float
    ) -> Dict[str, Any]:
        """Compute comprehensive risk assessment."""
        try:
            returns = np.array(self.returns_history[symbol])
            
            # Calculate volatility (annualized)
            volatility = np.std(returns) * np.sqrt(252)
            
            # Value at Risk (95% confidence)
            var_95 = np.percentile(returns, 5) if len(returns) > 0 else 0
            
            # Current exposure
            position = self.positions.get(symbol, 0)
            exposure = (position * current_price) / self.portfolio_value if self.portfolio_value > 0 else 0
            
            # Maximum drawdown
            prices = np.array(self.price_history[symbol])
            drawdown = self._calculate_max_drawdown(prices)
            
            # Sharpe ratio (simplified)
            sharpe = self._calculate_sharpe_ratio(returns)
            
            # Position size recommendation
            position_size = self._recommend_position_size(
                current_price,
                volatility,
                exposure
            )
            
            # Determine risk level
            risk_level = self._assess_risk_level(volatility, exposure, drawdown)
            
            return {
                'exposure': float(exposure),
                'volatility': float(volatility),
                'var_95': float(var_95),
                'max_drawdown': float(drawdown),
                'sharpe_ratio': float(sharpe),
                'position_size_recommendation': float(position_size),
                'risk_level': risk_level
            }
            
        except Exception as e:
            logger.error(f"Risk calculation error: {e}")
            return {}
    
    def _calculate_max_drawdown(self, prices: np.ndarray) -> float:
        """Calculate maximum drawdown."""
        if len(prices) < 2:
            return 0.0
        
        cummax = np.maximum.accumulate(prices)
        drawdown = (prices - cummax) / cummax
        
        return float(np.min(drawdown))
    
    def _calculate_sharpe_ratio(
        self,
        returns: np.ndarray,
        risk_free_rate: float = 0.02
    ) -> float:
        """Calculate Sharpe ratio."""
        if len(returns) < 2:
            return 0.0
        
        excess_returns = returns - (risk_free_rate / 252)
        
        if np.std(excess_returns) == 0:
            return 0.0
        
        return float(np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252))
    
    def _recommend_position_size(
        self,
        price: float,
        volatility: float,
        current_exposure: float
    ) -> float:
        """Recommend position size based on risk."""
        # Kelly Criterion inspired, but conservative
        max_position = Config.MAX_POSITION_SIZE
        
        # Adjust for volatility (higher vol = smaller position)
        if volatility > 0.5:
            max_position *= 0.5
        elif volatility > 0.3:
            max_position *= 0.75
        
        # Adjust for current exposure
        if current_exposure > Config.MAX_PORTFOLIO_EXPOSURE:
            max_position *= 0.5
        
        return max_position / price  # Number of shares
    
    def _assess_risk_level(
        self,
        volatility: float,
        exposure: float,
        drawdown: float
    ) -> str:
        """Assess overall risk level."""
        risk_score = 0
        
        # Volatility contribution
        if volatility > 0.5:
            risk_score += 3
        elif volatility > 0.3:
            risk_score += 2
        elif volatility > 0.2:
            risk_score += 1
        
        # Exposure contribution
        if exposure > 0.5:
            risk_score += 3
        elif exposure > 0.3:
            risk_score += 2
        elif exposure > 0.2:
            risk_score += 1
        
        # Drawdown contribution
        if abs(drawdown) > 0.15:
            risk_score += 3
        elif abs(drawdown) > 0.10:
            risk_score += 2
        elif abs(drawdown) > 0.05:
            risk_score += 1
        
        # Determine level
        if risk_score >= 6:
            return 'high'
        elif risk_score >= 3:
            return 'medium'
        else:
            return 'low'
    
    def update_position(self, symbol: str, quantity: int):
        """Update position for a symbol."""
        self.positions[symbol] = self.positions.get(symbol, 0) + quantity
    
    def update_portfolio_value(self, value: float):
        """Update total portfolio value."""
        self.portfolio_value = value


def main():
    """Run the Risk Management Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = RiskAgent()
    agent.run()


if __name__ == "__main__":
    main()
