"""
Market Data Agent - Ingests real-time stock data from APIs.
Focuses on the opening 10 minutes of trading.
"""
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
import requests
from loguru import logger
from core.config import Config
from core.event_bus import event_bus
from core.utils import is_market_open, is_in_trading_window
from data.db import db_manager


class MarketDataAgent:
    """
    Responsible for ingesting market data from multiple sources.
    Primary: Polygon.io
    Backup: Alpha Vantage
    Supplemental: Yahoo Finance
    """
    
    def __init__(self):
        self.name = "MarketDataAgent"
        self.polygon_api_key = Config.POLYGON_API_KEY
        self.alpha_vantage_api_key = Config.ALPHA_VANTAGE_API_KEY
        self.watchlist = Config.WATCHLIST
        self.poll_interval = 1  # 1 second during trading window
        self.last_fetch = {}
        
        logger.info(f"{self.name} initialized with watchlist: {self.watchlist}")
    
    def run(self):
        """Main agent loop."""
        logger.info(f"{self.name} starting...")
        
        while True:
            try:
                current_time = datetime.now(Config.TIMEZONE)
                
                # Check if we're in the trading window
                if is_in_trading_window(current_time):
                    logger.info(f"⏰ In trading window - fetching data for {len(self.watchlist)} symbols")
                    self._fetch_and_publish_data()
                    time.sleep(self.poll_interval)
                
                elif is_market_open(current_time):
                    # Market is open but outside our focus window
                    logger.debug("Market open but outside trading window")
                    time.sleep(60)  # Check every minute
                
                else:
                    # Market is closed
                    from core.utils import get_time_until_market_open
                    time_until_open = get_time_until_market_open(current_time)
                    logger.info(f"💤 Market closed. Next open in: {time_until_open}")
                    time.sleep(300)  # Check every 5 minutes
                    
            except KeyboardInterrupt:
                logger.info(f"{self.name} shutting down")
                break
            except Exception as e:
                logger.error(f"Error in {self.name}: {e}")
                time.sleep(5)
    
    def _fetch_and_publish_data(self):
        """Fetch data for all watchlist symbols and publish to event bus."""
        for symbol in self.watchlist:
            try:
                # Try Polygon first (most reliable for real-time)
                data = self._fetch_from_polygon(symbol)
                
                if not data and self.alpha_vantage_api_key:
                    # Fallback to Alpha Vantage
                    data = self._fetch_from_alpha_vantage(symbol)
                
                if not data:
                    # Last resort: Yahoo Finance
                    data = self._fetch_from_yahoo(symbol)
                
                if data:
                    # Publish to event bus
                    event_bus.publish_market_data(symbol, data)
                    
                    # Store in database
                    db_data = {
                        'symbol': symbol,
                        'timestamp': data['timestamp'],
                        'open': data['open'],
                        'high': data['high'],
                        'low': data['low'],
                        'close': data['close'],
                        'volume': data['volume'],
                        'vwap': data.get('vwap'),
                        'trade_count': data.get('trade_count')
                    }
                    db_manager.insert_market_data(db_data)
                    
                    logger.debug(f"📊 {symbol}: ${data['close']:.2f} Vol: {data['volume']:,}")
                    
            except Exception as e:
                logger.error(f"Failed to fetch data for {symbol}: {e}")
    
    def _fetch_from_polygon(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch real-time data from Polygon.io.
        Uses aggregates (bars) endpoint for minute-level data.
        """
        if not self.polygon_api_key:
            return {}
        
        try:
            # Get current minute bar
            now = datetime.now(Config.TIMEZONE)
            from_date = (now - timedelta(minutes=2)).strftime('%Y-%m-%d')
            to_date = now.strftime('%Y-%m-%d')
            
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/minute/{from_date}/{to_date}"
            params = {
                'apiKey': self.polygon_api_key,
                'adjusted': 'true',
                'sort': 'desc',
                'limit': 1
            }
            
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('results') and len(data['results']) > 0:
                bar = data['results'][0]
                
                return {
                    'timestamp': datetime.fromtimestamp(bar['t'] / 1000, tz=Config.TIMEZONE),
                    'price': bar['c'],
                    'open': bar['o'],
                    'high': bar['h'],
                    'low': bar['l'],
                    'close': bar['c'],
                    'volume': bar['v'],
                    'vwap': bar.get('vw'),
                    'trade_count': bar.get('n')
                }
            
            return {}
            
        except Exception as e:
            logger.warning(f"Polygon fetch failed for {symbol}: {e}")
            return {}
    
    def _fetch_from_alpha_vantage(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch data from Alpha Vantage.
        Uses GLOBAL_QUOTE for latest price.
        """
        if not self.alpha_vantage_api_key:
            return {}
        
        try:
            url = "https://www.alphavantage.co/query"
            params = {
                'function': 'GLOBAL_QUOTE',
                'symbol': symbol,
                'apikey': self.alpha_vantage_api_key
            }
            
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            
            if 'Global Quote' in data and data['Global Quote']:
                quote = data['Global Quote']
                
                return {
                    'timestamp': datetime.now(Config.TIMEZONE),
                    'price': float(quote['05. price']),
                    'open': float(quote['02. open']),
                    'high': float(quote['03. high']),
                    'low': float(quote['04. low']),
                    'close': float(quote['05. price']),
                    'volume': int(quote['06. volume'])
                }
            
            return {}
            
        except Exception as e:
            logger.warning(f"Alpha Vantage fetch failed for {symbol}: {e}")
            return {}
    
    def _fetch_from_yahoo(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch data from Yahoo Finance using yfinance.
        """
        try:
            import yfinance as yf
            
            ticker = yf.Ticker(symbol)
            
            # Get current day's intraday data
            df = ticker.history(period='1d', interval='1m')
            
            if not df.empty:
                latest = df.iloc[-1]
                
                return {
                    'timestamp': datetime.now(Config.TIMEZONE),
                    'price': float(latest['Close']),
                    'open': float(latest['Open']),
                    'high': float(latest['High']),
                    'low': float(latest['Low']),
                    'close': float(latest['Close']),
                    'volume': int(latest['Volume'])
                }
            
            return {}
            
        except Exception as e:
            logger.warning(f"Yahoo Finance fetch failed for {symbol}: {e}")
            return {}
    
    def get_historical_data(
        self,
        symbol: str,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical data for backtesting/analysis.
        """
        try:
            # Try database first
            data = db_manager.get_latest_market_data(symbol, limit=days * 390)
            
            if data:
                return data
            
            # Fallback to API
            if self.polygon_api_key:
                return self._fetch_historical_polygon(symbol, days)
            
            return []
            
        except Exception as e:
            logger.error(f"Failed to get historical data for {symbol}: {e}")
            return []
    
    def _fetch_historical_polygon(
        self,
        symbol: str,
        days: int
    ) -> List[Dict[str, Any]]:
        """Fetch historical data from Polygon."""
        try:
            from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            to_date = datetime.now().strftime('%Y-%m-%d')
            
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{to_date}"
            params = {
                'apiKey': self.polygon_api_key,
                'adjusted': 'true',
                'sort': 'asc'
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('results'):
                return [
                    {
                        'timestamp': datetime.fromtimestamp(bar['t'] / 1000, tz=Config.TIMEZONE),
                        'open': bar['o'],
                        'high': bar['h'],
                        'low': bar['l'],
                        'close': bar['c'],
                        'volume': bar['v'],
                        'vwap': bar.get('vw')
                    }
                    for bar in data['results']
                ]
            
            return []
            
        except Exception as e:
            logger.error(f"Historical Polygon fetch failed: {e}")
            return []


def main():
    """Run the Market Data Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = MarketDataAgent()
    agent.run()


if __name__ == "__main__":
    main()
