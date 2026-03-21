"""
Alert Agent - Sends notifications and alerts based on system events.
"""
from datetime import datetime
from typing import Dict, Any
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from data.db import db_manager


class AlertAgent:
    """
    Monitors trades, anomalies, and decisions to send alerts.
    Subscribes to: trades, anomalies, decisions
    Publishes: alerts
    """
    
    def __init__(self):
        self.name = "AlertAgent"
        logger.info(f"{self.name} initialized")
    
    def run(self):
        """Main agent loop - subscribe to events."""
        logger.info(f"{self.name} starting...")
        
        event_bus.subscribe(
            EventType.TRADES,
            f"{self.name}_trades",
            self.handle_trade
        )
        event_bus.subscribe(
            EventType.ANOMALIES,
            f"{self.name}_anomalies",
            self.handle_anomaly
        )
        event_bus.subscribe(
            EventType.DECISIONS,
            f"{self.name}_decisions",
            self.handle_decision
        )
    
    def handle_trade(self, trade: Dict[str, Any]):
        """Send alert for executed trade."""
        try:
            alert = {
                'timestamp': datetime.now(Config.TIMEZONE),
                'alert_type': 'trade',
                'severity': 'INFO',
                'symbol': trade['symbol'],
                'message': f"Trade executed: {trade['action']} {trade['quantity']} {trade['symbol']} @ ${trade['price']:.2f}",
                'details': trade
            }
            event_bus.publish_alert(alert)
            db_manager.insert_alert(alert)
            logger.info(f"🔔 Trade alert: {alert['message']}")
        except Exception as e:
            logger.error(f"Trade alert error: {e}")
    
    def handle_anomaly(self, anomaly: Dict[str, Any]):
        """Send alert for detected anomaly."""
        try:
            alert = {
                'timestamp': datetime.now(Config.TIMEZONE),
                'alert_type': 'anomaly',
                'severity': 'WARNING' if anomaly.get('severity') != 'high' else 'CRITICAL',
                'symbol': anomaly['symbol'],
                'message': f"Anomaly detected: {anomaly['anomaly_type']} ({anomaly['details']})",
                'details': anomaly
            }
            event_bus.publish_alert(alert)
            db_manager.insert_alert(alert)
            logger.warning(f"🔔 Anomaly alert: {alert['message']}")
        except Exception as e:
            logger.error(f"Anomaly alert error: {e}")
    
    def handle_decision(self, decision: Dict[str, Any]):
        """Send alert for high-confidence decisions."""
        try:
            if decision['confidence'] >= 0.85:
                alert = {
                    'timestamp': datetime.now(Config.TIMEZONE),
                    'alert_type': 'decision',
                    'severity': 'INFO',
                    'symbol': decision['symbol'],
                    'message': f"High-confidence decision: {decision['action']} {decision['symbol']} ({decision['confidence']:.0%})",
                    'details': decision
                }
                event_bus.publish_alert(alert)
                db_manager.insert_alert(alert)
                logger.info(f"🔔 Decision alert: {alert['message']}")
        except Exception as e:
            logger.error(f"Decision alert error: {e}")


def main():
    """Run the Alert Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = AlertAgent()
    agent.run()


if __name__ == "__main__":
    main()
