"""
Debate Agents - Bull, Bear, and Judge agents for consensus building.
"""
from datetime import datetime
from typing import Dict, Any, List
from collections import defaultdict
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from core.schemas import Action


class DebateSystem:
    """
    Multi-agent debate system for trading decisions.
    Bull Agent argues for BUY, Bear Agent argues for SELL, Judge resolves.
    """
    
    def __init__(self):
        self.name = "DebateSystem"
        self.signals_cache = defaultdict(dict)
        
        logger.info(f"{self.name} initialized")
    
    def run(self):
        """Main loop - subscribe to signals and conduct debates."""
        logger.info(f"{self.name} starting...")
        
        # Subscribe to all signal types
        event_bus.subscribe(
            EventType.TECHNICAL_SIGNALS,
            f"{self.name}_technical",
            lambda data: self._cache_signal('technical', data)
        )
        
        event_bus.subscribe(
            EventType.SENTIMENT_SIGNALS,
            f"{self.name}_sentiment",
            lambda data: self._cache_signal('sentiment', data)
        )
        
        event_bus.subscribe(
            EventType.ANOMALIES,
            f"{self.name}_anomaly",
            lambda data: self._cache_signal('anomaly', data)
        )
        
        event_bus.subscribe(
            EventType.RISK_ASSESSMENTS,
            f"{self.name}_risk",
            self.conduct_debate
        )
    
    def _cache_signal(self, signal_type: str, data: Dict[str, Any]):
        """Cache signals for debate analysis."""
        symbol = data.get('symbol')
        if symbol:
            self.signals_cache[symbol][signal_type] = data
    
    def conduct_debate(self, risk_data: Dict[str, Any]):
        """Conduct debate between Bull, Bear, and Judge agents."""
        try:
            symbol = risk_data['symbol']
            
            # Get all cached signals for this symbol
            signals = self.signals_cache.get(symbol, {})
            
            if not signals or 'technical' not in signals:
                return
            
            # Bull Agent argues for BUY
            bull_result = self._bull_agent(symbol, signals, risk_data)
            
            # Bear Agent argues for SELL
            bear_result = self._bear_agent(symbol, signals, risk_data)
            
            # Judge Agent resolves
            judge_result = self._judge_agent(symbol, bull_result, bear_result, risk_data)
            
            # Publish debate outcome
            debate_outcome = {
                'symbol': symbol,
                'timestamp': datetime.now(Config.TIMEZONE),
                'bull_score': bull_result['score'],
                'bull_arguments': bull_result['arguments'],
                'bear_score': bear_result['score'],
                'bear_arguments': bear_result['arguments'],
                'judge_decision': judge_result['decision'],
                'judge_confidence': judge_result['confidence'],
                'judge_rationale': judge_result['rationale']
            }
            
            event_bus.publish(EventType.DEBATE_OUTCOME, debate_outcome)
            
            logger.info(
                f"🗣️  {symbol} Debate: Bull={bull_result['score']:.2f}, "
                f"Bear={bear_result['score']:.2f}, "
                f"Judge={judge_result['decision']} ({judge_result['confidence']:.0%})"
            )
            
        except Exception as e:
            logger.error(f"Debate error: {e}")
    
    def _bull_agent(
        self,
        symbol: str,
        signals: Dict[str, Any],
        risk_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Bull Agent - Builds case for BUY.
        Looks for positive signals.
        """
        score = 0.0
        arguments = []
        
        # Technical analysis
        technical = signals.get('technical', {})
        
        if technical.get('signal') == 'bullish':
            score += 0.3
            arguments.append("Technical indicators show bullish momentum")
        
        rsi = technical.get('rsi', 50)
        if rsi < Config.RSI_OVERSOLD:
            score += 0.2
            arguments.append(f"RSI oversold at {rsi:.1f} - potential bounce")
        
        macd = technical.get('macd', 0)
        if macd > 0:
            score += 0.15
            arguments.append("MACD positive momentum")
        
        # Sentiment analysis
        sentiment = signals.get('sentiment', {})
        sent_score = sentiment.get('sentiment_score', 0)
        
        if sent_score > Config.SENTIMENT_POSITIVE_THRESHOLD:
            score += 0.2
            arguments.append(f"Positive news sentiment ({sent_score:.2f})")
        
        # Risk assessment
        if risk_data.get('risk_level') == 'low':
            score += 0.15
            arguments.append("Low risk environment")
        
        # Normalize score
        score = min(score, 1.0)
        
        if not arguments:
            arguments.append("No strong bullish signals detected")
        
        return {
            'score': score,
            'arguments': arguments
        }
    
    def _bear_agent(
        self,
        symbol: str,
        signals: Dict[str, Any],
        risk_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Bear Agent - Builds case for SELL.
        Looks for negative signals.
        """
        score = 0.0
        arguments = []
        
        # Technical analysis
        technical = signals.get('technical', {})
        
        if technical.get('signal') == 'bearish':
            score += 0.3
            arguments.append("Technical indicators show bearish pressure")
        
        rsi = technical.get('rsi', 50)
        if rsi > Config.RSI_OVERBOUGHT:
            score += 0.2
            arguments.append(f"RSI overbought at {rsi:.1f} - potential pullback")
        
        macd = technical.get('macd', 0)
        if macd < 0:
            score += 0.15
            arguments.append("MACD negative momentum")
        
        # Sentiment analysis
        sentiment = signals.get('sentiment', {})
        sent_score = sentiment.get('sentiment_score', 0)
        
        if sent_score < Config.SENTIMENT_NEGATIVE_THRESHOLD:
            score += 0.2
            arguments.append(f"Negative news sentiment ({sent_score:.2f})")
        
        # Risk assessment
        if risk_data.get('risk_level') == 'high':
            score += 0.2
            arguments.append("High risk environment - exposure should be reduced")
        
        # Anomaly detection
        anomaly = signals.get('anomaly', {})
        if anomaly.get('severity') in ['high', 'medium']:
            score += 0.15
            arguments.append(f"Market anomaly detected: {anomaly.get('details')}")
        
        # Normalize score
        score = min(score, 1.0)
        
        if not arguments:
            arguments.append("No strong bearish signals detected")
        
        return {
            'score': score,
            'arguments': arguments
        }
    
    def _judge_agent(
        self,
        symbol: str,
        bull_result: Dict[str, Any],
        bear_result: Dict[str, Any],
        risk_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Judge Agent - Resolves debate and makes final decision.
        Weighs arguments from both sides.
        """
        bull_score = bull_result['score']
        bear_score = bear_result['score']
        
        # Calculate decision
        if bull_score > bear_score + 0.2:
            decision = Action.BUY
            confidence = bull_score
            rationale = f"Bull arguments stronger ({bull_score:.2f} vs {bear_score:.2f}). " + \
                       " ".join(bull_result['arguments'][:2])
        
        elif bear_score > bull_score + 0.2:
            decision = Action.SELL
            confidence = bear_score
            rationale = f"Bear arguments stronger ({bear_score:.2f} vs {bull_score:.2f}). " + \
                       " ".join(bear_result['arguments'][:2])
        
        else:
            decision = Action.HOLD
            confidence = 0.5
            rationale = f"Arguments balanced ({bull_score:.2f} vs {bear_score:.2f}). " + \
                       "Insufficient conviction for action."
        
        # Apply risk filter
        if risk_data.get('risk_level') == 'high' and decision == Action.BUY:
            decision = Action.HOLD
            confidence *= 0.7
            rationale += " However, high risk environment suggests caution."
        
        return {
            'decision': decision.value,
            'confidence': confidence,
            'rationale': rationale
        }


def main():
    """Run the Debate System."""
    from core.utils import setup_logging
    setup_logging()
    
    system = DebateSystem()
    system.run()


if __name__ == "__main__":
    main()
