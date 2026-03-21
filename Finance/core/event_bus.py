"""
Event bus implementation using Redis Streams.
Provides pub/sub functionality for agent communication.
"""
import json
import redis
from typing import Optional, Dict, Any, Callable, List
from datetime import datetime
from loguru import logger
from core.config import Config
from core.schemas import EventType


class EventBus:
    """Redis Streams-based event bus for agent communication."""
    
    def __init__(self):
        """Initialize Redis connection."""
        self.redis_client = redis.Redis(
            host=Config.REDIS_HOST,
            port=Config.REDIS_PORT,
            db=Config.REDIS_DB,
            decode_responses=True
        )
        self.consumer_group = "trading_agents"
        self._initialize_streams()
    
    def _initialize_streams(self):
        """Initialize Redis streams for each event type."""
        for event_type in EventType:
            stream_name = f"stream:{event_type.value}"
            try:
                # Create consumer group if it doesn't exist
                self.redis_client.xgroup_create(
                    stream_name,
                    self.consumer_group,
                    id='0',
                    mkstream=True
                )
                logger.info(f"Initialized stream: {stream_name}")
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    logger.warning(f"Stream initialization warning: {e}")
    
    def publish(self, event_type: EventType, data: Dict[str, Any]) -> str:
        """
        Publish an event to the specified stream.
        
        Args:
            event_type: Type of event
            data: Event data (will be JSON serialized)
            
        Returns:
            Message ID
        """
        stream_name = f"stream:{event_type.value}"
        
        # Serialize data
        serialized_data = {
            'data': json.dumps(data, default=str),
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # Publish to stream
        message_id = self.redis_client.xadd(
            stream_name,
            serialized_data,
            maxlen=Config.EVENT_STREAM_MAXLEN
        )
        
        logger.debug(f"Published {event_type.value}: {message_id}")
        return message_id
    
    def subscribe(
        self,
        event_type: EventType,
        consumer_name: str,
        callback: Callable[[Dict[str, Any]], None],
        block_ms: int = 5000,
        count: int = 10
    ):
        """
        Subscribe to an event stream and process messages.
        
        Args:
            event_type: Type of event to subscribe to
            consumer_name: Unique consumer identifier
            callback: Function to call for each message
            block_ms: Blocking timeout in milliseconds
            count: Max messages to read per call
        """
        stream_name = f"stream:{event_type.value}"
        
        logger.info(f"Consumer {consumer_name} subscribing to {event_type.value}")
        
        while True:
            try:
                # Read from stream
                messages = self.redis_client.xreadgroup(
                    self.consumer_group,
                    consumer_name,
                    {stream_name: '>'},
                    count=count,
                    block=block_ms
                )
                
                if not messages:
                    continue
                
                for stream, message_list in messages:
                    for message_id, message_data in message_list:
                        try:
                            # Deserialize and process
                            data = json.loads(message_data['data'])
                            callback(data)
                            
                            # Acknowledge message
                            self.redis_client.xack(
                                stream_name,
                                self.consumer_group,
                                message_id
                            )
                        except Exception as e:
                            logger.error(f"Error processing message {message_id}: {e}")
                            # Optionally: move to dead letter queue
                            
            except KeyboardInterrupt:
                logger.info(f"Consumer {consumer_name} shutting down")
                break
            except Exception as e:
                logger.error(f"Subscription error: {e}")
    
    def get_stream_info(self, event_type: EventType) -> Dict[str, Any]:
        """Get information about a stream."""
        stream_name = f"stream:{event_type.value}"
        try:
            info = self.redis_client.xinfo_stream(stream_name)
            return info
        except redis.ResponseError:
            return {}
    
    def get_latest_events(
        self,
        event_type: EventType,
        count: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get the latest events from a stream.
        
        Args:
            event_type: Type of event
            count: Number of events to retrieve
            
        Returns:
            List of events
        """
        stream_name = f"stream:{event_type.value}"
        
        try:
            messages = self.redis_client.xrevrange(
                stream_name,
                count=count
            )
            
            events = []
            for message_id, message_data in messages:
                data = json.loads(message_data['data'])
                data['_message_id'] = message_id
                events.append(data)
            
            return events
        except Exception as e:
            logger.error(f"Error retrieving events: {e}")
            return []
    
    def publish_market_data(self, symbol: str, data: Dict[str, Any]):
        """Convenience method for publishing market data."""
        event_data = {'symbol': symbol, **data}
        return self.publish(EventType.MARKET_DATA, event_data)
    
    def publish_technical_signals(self, symbol: str, signals: Dict[str, Any]):
        """Convenience method for publishing technical signals."""
        event_data = {'symbol': symbol, **signals}
        return self.publish(EventType.TECHNICAL_SIGNALS, event_data)
    
    def publish_sentiment(self, symbol: str, sentiment: Dict[str, Any]):
        """Convenience method for publishing sentiment."""
        event_data = {'symbol': symbol, **sentiment}
        return self.publish(EventType.SENTIMENT_SIGNALS, event_data)
    
    def publish_anomaly(self, symbol: str, anomaly: Dict[str, Any]):
        """Convenience method for publishing anomaly."""
        event_data = {'symbol': symbol, **anomaly}
        return self.publish(EventType.ANOMALIES, event_data)
    
    def publish_risk_assessment(self, symbol: str, assessment: Dict[str, Any]):
        """Convenience method for publishing risk assessment."""
        event_data = {'symbol': symbol, **assessment}
        return self.publish(EventType.RISK_ASSESSMENTS, event_data)
    
    def publish_decision(self, symbol: str, decision: Dict[str, Any]):
        """Convenience method for publishing decision."""
        event_data = {'symbol': symbol, **decision}
        return self.publish(EventType.DECISIONS, event_data)
    
    def publish_trade(self, trade: Dict[str, Any]):
        """Convenience method for publishing trade."""
        return self.publish(EventType.TRADES, trade)
    
    def publish_alert(self, alert: Dict[str, Any]):
        """Convenience method for publishing alert."""
        return self.publish(EventType.ALERTS, alert)
    
    def clear_stream(self, event_type: EventType):
        """Clear all messages from a stream (for testing)."""
        stream_name = f"stream:{event_type.value}"
        self.redis_client.delete(stream_name)
        self._initialize_streams()
        logger.info(f"Cleared stream: {stream_name}")
    
    def health_check(self) -> bool:
        """Check if Redis connection is healthy."""
        try:
            self.redis_client.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False


# Global event bus instance
event_bus = EventBus()
