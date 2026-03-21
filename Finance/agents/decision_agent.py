"""
Decision Agent - Makes final trading decisions based on all signals.
"""
from datetime import datetime
from typing import Dict, Any
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from core.schemas import Action
from core.utils import calculate_position_size, calculate_stop_loss, calculate_take_profit
from data.db import db_manager


class DecisionAgent:
    """
    Makes trading decisions based on debate outcomes and signal fusion.
    Subscribes to: debate_outcome
    Publishes: decisions
    """
    
    def __init__(self):
        self.name = "DecisionAgent"
        
        logger.info(f"{self.name} initialized")
    
    def run(self):
        """Main agent loop - subscribe to debate outcomes."""
        logger.info(f"{self.name} starting...")
        
        event_bus.subscribe(
            EventType.DEBATE_OUTCOME,
            f"{self.name}_consumer",
            self.make_decision
        )
    
    def make_decision(self, debate_data: Dict[str, Any]):
        """Make trading decision based on debate outcome."""
        try:
            symbol = debate_data['symbol']
            judge_decision = debate_data['judge_decision']
            confidence = debate_data['judge_confidence']
            
            # Filter by minimum confidence threshold
            if confidence < Config.MIN_CONFIDENCE_THRESHOLD:
                logger.debug(
                    f"{symbol}: Confidence {confidence:.0%} below threshold "
                    f"{Config.MIN_CONFIDENCE_THRESHOLD:.0%}"
                )
                return
            
            # Only act on BUY/SELL decisions during trading window
            from core.utils import is_in_trading_window
            
            if not is_in_trading_window():
                logger.debug(f"{symbol}: Outside trading window")
                return
            
            if judge_decision == Action.HOLD.value:
                logger.debug(f"{symbol}: Decision is HOLD")
                return
            
            # Get current price from latest market data
            latest_data = event_bus.get_latest_events(EventType.MARKET_DATA, count=50)
            
            symbol_data = [d for d in latest_data if d.get('symbol') == symbol]
            
            if not symbol_data:
                logger.warning(f"{symbol}: No market data available")
                return
            
            current_price = symbol_data[0]['close']
            
            # Calculate position size
            quantity = calculate_position_size(
                current_price,
                Config.MAX_POSITION_SIZE,
                confidence
            )
            
            # Calculate stop loss and take profit
            stop_loss = calculate_stop_loss(current_price, judge_decision)
            take_profit = calculate_take_profit(current_price, judge_decision)
            
            # Create decision event
            decision = {
                'symbol': symbol,
                'timestamp': datetime.now(Config.TIMEZONE),
                'action': judge_decision,
                'confidence': confidence,
                'reason': debate_data['judge_rationale'],
                'supporting_signals': {
                    'bull_score': debate_data['bull_score'],
                    'bear_score': debate_data['bear_score']
                },
                'position_size': quantity,
                'entry_price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit
            }
            
            # Publish decision
            event_bus.publish_decision(symbol, decision)
            
            # Store in database
            db_data = {
                'symbol': symbol,
                'timestamp': decision['timestamp'],
                'action': judge_decision,
                'confidence': confidence,
                'reason': decision['reason'],
                'position_size': quantity,
                'entry_price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'supporting_signals': decision['supporting_signals']
            }
            db_manager.insert_decision(db_data)
            
            logger.info(
                f"✅ {symbol} DECISION: {judge_decision} {quantity} shares @ ${current_price:.2f} "
                f"(confidence: {confidence:.0%})"
            )
            
        except Exception as e:
            logger.error(f"Decision making error: {e}")


def main():
    """Run the Decision Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = DecisionAgent()
    agent.run()


if __name__ == "__main__":
    main()
