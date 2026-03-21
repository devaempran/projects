"""
Database layer using SQLAlchemy and TimescaleDB.
"""
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, JSON, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from loguru import logger
from core.config import Config

# Create base class
Base = declarative_base()

# Database Models

class MarketData(Base):
    """Market data time-series table."""
    __tablename__ = 'market_data'
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    vwap = Column(Float)
    trade_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_symbol_timestamp', 'symbol', 'timestamp'),
    )


class Signal(Base):
    """Technical and sentiment signals table."""
    __tablename__ = 'signals'
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    signal_type = Column(String(20))  # 'technical', 'sentiment', 'anomaly'
    
    # Technical indicators
    rsi = Column(Float)
    macd = Column(Float)
    macd_signal = Column(Float)
    macd_hist = Column(Float)
    sma_20 = Column(Float)
    sma_50 = Column(Float)
    ema_12 = Column(Float)
    ema_26 = Column(Float)
    bollinger_upper = Column(Float)
    bollinger_lower = Column(Float)
    
    # Sentiment
    sentiment_score = Column(Float)
    sentiment_magnitude = Column(Float)
    
    # Anomaly
    anomaly_score = Column(Float)
    anomaly_type = Column(String(50))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_signal_symbol_timestamp', 'symbol', 'timestamp'),
    )


class Decision(Base):
    """Trading decisions table."""
    __tablename__ = 'decisions'
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    action = Column(String(10), nullable=False)  # 'BUY', 'SELL', 'HOLD'
    confidence = Column(Float, nullable=False)
    reason = Column(Text)
    position_size = Column(Float)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    supporting_signals = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class Trade(Base):
    """Executed trades table."""
    __tablename__ = 'trades'
    
    id = Column(Integer, primary_key=True)
    trade_id = Column(String(50), unique=True, nullable=False)
    symbol = Column(String(10), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    action = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    commission = Column(Float, default=0.0)
    total_cost = Column(Float, nullable=False)
    portfolio_value = Column(Float)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    """Alerts table."""
    __tablename__ = 'alerts'
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    symbol = Column(String(10), index=True)
    message = Column(Text, nullable=False)
    details = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class Position(Base):
    """Current positions table."""
    __tablename__ = 'positions'
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), unique=True, nullable=False)
    quantity = Column(Integer, nullable=False)
    avg_entry_price = Column(Float, nullable=False)
    current_price = Column(Float)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Database Manager

class DatabaseManager:
    """Manages database connections and operations."""
    
    def __init__(self, database_url: Optional[str] = None):
        """Initialize database connection."""
        self.database_url = database_url or Config.DATABASE_URL
        self.engine = create_engine(self.database_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
    def init_db(self):
        """Initialize database schema."""
        try:
            Base.metadata.create_all(self.engine)
            self._enable_timescaledb()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            raise
    
    def _enable_timescaledb(self):
        """Enable TimescaleDB hypertables for time-series data."""
        with self.engine.connect() as conn:
            try:
                # Check if TimescaleDB extension exists
                result = conn.execute(
                    "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"
                )
                if not result.fetchone():
                    logger.warning("TimescaleDB extension not found, skipping hypertable creation")
                    return
                
                # Create hypertables
                for table in ['market_data', 'signals', 'decisions', 'trades', 'alerts']:
                    try:
                        conn.execute(
                            f"SELECT create_hypertable('{table}', 'timestamp', if_not_exists => TRUE)"
                        )
                        logger.info(f"Created hypertable: {table}")
                    except Exception as e:
                        logger.warning(f"Hypertable creation skipped for {table}: {e}")
                
                conn.commit()
            except Exception as e:
                logger.warning(f"TimescaleDB setup warning: {e}")
    
    def get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()
    
    # Market Data Operations
    
    def insert_market_data(self, data: Dict[str, Any]):
        """Insert market data record."""
        session = self.get_session()
        try:
            record = MarketData(**data)
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to insert market data: {e}")
        finally:
            session.close()
    
    def get_latest_market_data(
        self,
        symbol: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get latest market data for a symbol."""
        session = self.get_session()
        try:
            records = session.query(MarketData).filter(
                MarketData.symbol == symbol
            ).order_by(
                MarketData.timestamp.desc()
            ).limit(limit).all()
            
            return [self._record_to_dict(r) for r in records]
        finally:
            session.close()
    
    # Signal Operations
    
    def insert_signal(self, data: Dict[str, Any]):
        """Insert signal record."""
        session = self.get_session()
        try:
            record = Signal(**data)
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to insert signal: {e}")
        finally:
            session.close()
    
    # Decision Operations
    
    def insert_decision(self, data: Dict[str, Any]):
        """Insert decision record."""
        session = self.get_session()
        try:
            record = Decision(**data)
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to insert decision: {e}")
        finally:
            session.close()
    
    def get_recent_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent decisions."""
        session = self.get_session()
        try:
            records = session.query(Decision).order_by(
                Decision.timestamp.desc()
            ).limit(limit).all()
            
            return [self._record_to_dict(r) for r in records]
        finally:
            session.close()
    
    # Trade Operations
    
    def insert_trade(self, data: Dict[str, Any]):
        """Insert trade record."""
        session = self.get_session()
        try:
            record = Trade(**data)
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to insert trade: {e}")
        finally:
            session.close()
    
    def get_trades(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get trades, optionally filtered by symbol."""
        session = self.get_session()
        try:
            query = session.query(Trade)
            if symbol:
                query = query.filter(Trade.symbol == symbol)
            
            records = query.order_by(Trade.timestamp.desc()).all()
            return [self._record_to_dict(r) for r in records]
        finally:
            session.close()
    
    # Alert Operations
    
    def insert_alert(self, data: Dict[str, Any]):
        """Insert alert record."""
        session = self.get_session()
        try:
            record = Alert(**data)
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to insert alert: {e}")
        finally:
            session.close()
    
    def get_recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        session = self.get_session()
        try:
            records = session.query(Alert).order_by(
                Alert.timestamp.desc()
            ).limit(limit).all()
            
            return [self._record_to_dict(r) for r in records]
        finally:
            session.close()
    
    # Position Operations
    
    def upsert_position(self, data: Dict[str, Any]):
        """Insert or update position."""
        session = self.get_session()
        try:
            position = session.query(Position).filter(
                Position.symbol == data['symbol']
            ).first()
            
            if position:
                for key, value in data.items():
                    setattr(position, key, value)
            else:
                position = Position(**data)
                session.add(position)
            
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to upsert position: {e}")
        finally:
            session.close()
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all current positions."""
        session = self.get_session()
        try:
            records = session.query(Position).all()
            return [self._record_to_dict(r) for r in records]
        finally:
            session.close()
    
    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get position for a specific symbol."""
        session = self.get_session()
        try:
            record = session.query(Position).filter(
                Position.symbol == symbol
            ).first()
            
            return self._record_to_dict(record) if record else None
        finally:
            session.close()
    
    # Utility Methods
    
    def _record_to_dict(self, record) -> Dict[str, Any]:
        """Convert SQLAlchemy record to dictionary."""
        if record is None:
            return {}
        
        return {
            column.name: getattr(record, column.name)
            for column in record.__table__.columns
        }
    
    def health_check(self) -> bool:
        """Check database connection health."""
        try:
            with self.engine.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False


# Global database manager instance
db_manager = DatabaseManager()
