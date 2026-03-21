"""
Execution Agent - Simulates trade execution and tracks performance.
"""
from datetime import datetime
from typing import Dict, Any
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from data.db import db_manager
import uuid


class ExecutionAgent:
    """
    Simulates trade execution (paper trading).
    Subscribes to: decisions
    Publishes: trades
    """
    
    def __init__(self):
        self.name = "ExecutionAgent"
        self.portfolio_value = 100000.0  # Starting capital
        self.positions = {}  # symbol -> quantity
        self.trade_history = []
        
        logger.info(f"{self.name} initialized with ${self.portfolio_value:,.2f}")
    
    def run(self):
        """Main agent loop - subscribe to decisions."""
        logger.info(f"{self.name} starting...")
        
        event_bus.subscribe(
            EventType.DECISIONS,
            f"{self.name}_consumer",
            self.execute_trade
        )
    
    def execute_trade(self, decision: Dict[str, Any]):
        """Simulate trade execution and update portfolio."""
        try:
            symbol = decision['symbol']
            action = decision['action']
            quantity = int(decision['position_size'])
            price = float(decision['entry_price'])
            timestamp = decision.get('timestamp', datetime.now(Config.TIMEZONE))
            
            if quantity <= 0:
                logger.debug(f"{symbol}: No shares to trade")
                return
            
            # Simulate trade
            trade_id = str(uuid.uuid4())
            commission = 0.0  # No commission for paper trading
            total_cost = price * quantity + commission
            
            # Update positions
            if action == 'BUY':
                self.positions[symbol] = self.positions.get(symbol, 0) + quantity
                self.portfolio_value -= total_cost
            elif action == 'SELL':
                self.positions[symbol] = self.positions.get(symbol, 0) - quantity
                self.portfolio_value += total_cost
            
            # Calculate PnL (simplified)
            pnl = 0.0
            pnl_pct = 0.0
            if action == 'SELL':
                # Find matching buy (FIFO)
                # For simplicity, assume all sells are closing positions
                buy_price = price  # In real system, track lots
                pnl = (price - buy_price) * quantity
                pnl_pct = pnl / (buy_price * quantity) if buy_price > 0 else 0
            
            # Create trade event
            trade = {
                'trade_id': trade_id,
                'symbol': symbol,
                'timestamp': timestamp,
                'action': action,
                'quantity': quantity,
                'price': price,
                'commission': commission,
                'total_cost': total_cost,
                'portfolio_value': self.portfolio_value,
                'pnl': pnl,
                'pnl_pct': pnl_pct
            }
            
            # Publish trade event
            event_bus.publish_trade(trade)
            
            # Store in database
            db_manager.insert_trade(trade)
            
            logger.info(
                f"💰 {symbol} {action} {quantity} @ ${price:.2f} | Portfolio: ${self.portfolio_value:,.2f}"
            )
            
        except Exception as e:
            logger.error(f"Trade execution error: {e}")


def main():
    """Run the Execution Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = ExecutionAgent()
    agent.run()


if __name__ == "__main__":
    main()
