"""
Sentiment Analysis Agent - Analyzes news and social sentiment.
"""
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
import requests
from loguru import logger
from core.config import Config
from core.event_bus import event_bus, EventType
from data.db import db_manager


class SentimentAgent:
    """
    Analyzes news sentiment for stocks.
    Subscribes to: market_data (for symbol tracking)
    Publishes: sentiment_signals
    """
    
    def __init__(self):
        self.name = "SentimentAgent"
        self.news_api_key = Config.NEWS_API_KEY
        self.watchlist = Config.WATCHLIST
        self.last_fetch = {}
        self.fetch_interval = 300  # 5 minutes
        
        logger.info(f"{self.name} initialized")
    
    def run(self):
        """Main agent loop."""
        logger.info(f"{self.name} starting...")
        
        while True:
            try:
                from core.utils import is_in_trading_window
                
                if is_in_trading_window():
                    self._fetch_and_analyze_sentiment()
                
                time.sleep(self.fetch_interval)
                
            except KeyboardInterrupt:
                logger.info(f"{self.name} shutting down")
                break
            except Exception as e:
                logger.error(f"Error in {self.name}: {e}")
                time.sleep(60)
    
    def _fetch_and_analyze_sentiment(self):
        """Fetch news and analyze sentiment for watchlist."""
        for symbol in self.watchlist:
            try:
                # Check if we've fetched recently
                now = datetime.now()
                if symbol in self.last_fetch:
                    elapsed = (now - self.last_fetch[symbol]).seconds
                    if elapsed < self.fetch_interval:
                        continue
                
                # Fetch news
                news = self._fetch_news(symbol)
                
                if news:
                    # Analyze sentiment
                    sentiment = self._analyze_sentiment(news)
                    
                    # Publish to event bus
                    sentiment_data = {
                        'symbol': symbol,
                        'timestamp': now,
                        'sentiment_score': sentiment['score'],
                        'magnitude': sentiment['magnitude'],
                        'source': 'newsapi',
                        'news_count': len(news),
                        'headline': news[0].get('title', '') if news else ''
                    }
                    
                    event_bus.publish_sentiment(symbol, sentiment_data)
                    
                    # Store in database
                    db_data = {
                        'symbol': symbol,
                        'timestamp': now,
                        'signal_type': 'sentiment',
                        'sentiment_score': sentiment['score'],
                        'sentiment_magnitude': sentiment['magnitude']
                    }
                    db_manager.insert_signal(db_data)
                    
                    logger.info(
                        f"📰 {symbol} Sentiment: {sentiment['score']:.2f} "
                        f"({sentiment['label']}) from {len(news)} articles"
                    )
                    
                    self.last_fetch[symbol] = now
                    
            except Exception as e:
                logger.error(f"Error analyzing sentiment for {symbol}: {e}")
    
    def _fetch_news(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch news articles for a symbol."""
        if not self.news_api_key:
            return []
        
        try:
            # Get company name mapping (simplified)
            company_names = {
                'AAPL': 'Apple',
                'MSFT': 'Microsoft',
                'GOOGL': 'Google',
                'AMZN': 'Amazon',
                'TSLA': 'Tesla',
                'NVDA': 'Nvidia',
                'META': 'Meta',
                'JPM': 'JPMorgan',
                'V': 'Visa',
                'WMT': 'Walmart'
            }
            
            query = company_names.get(symbol, symbol)
            
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': query,
                'apiKey': self.news_api_key,
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 10,
                'from': (datetime.now() - timedelta(hours=24)).isoformat()
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('articles'):
                return data['articles']
            
            return []
            
        except Exception as e:
            logger.warning(f"News fetch failed for {symbol}: {e}")
            return []
    
    def _analyze_sentiment(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze sentiment of news articles.
        Returns sentiment score from -1 (negative) to 1 (positive).
        """
        try:
            from textblob import TextBlob
            
            sentiments = []
            
            for article in articles:
                text = f"{article.get('title', '')} {article.get('description', '')}"
                
                if text.strip():
                    blob = TextBlob(text)
                    sentiments.append(blob.sentiment.polarity)
            
            if not sentiments:
                return {
                    'score': 0.0,
                    'magnitude': 0.0,
                    'label': 'neutral'
                }
            
            # Calculate average sentiment
            avg_sentiment = sum(sentiments) / len(sentiments)
            magnitude = abs(avg_sentiment)
            
            # Determine label
            if avg_sentiment > Config.SENTIMENT_POSITIVE_THRESHOLD:
                label = 'positive'
            elif avg_sentiment < Config.SENTIMENT_NEGATIVE_THRESHOLD:
                label = 'negative'
            else:
                label = 'neutral'
            
            return {
                'score': float(avg_sentiment),
                'magnitude': float(magnitude),
                'label': label
            }
            
        except ImportError:
            logger.warning("TextBlob not installed, using fallback sentiment")
            return self._fallback_sentiment(articles)
        except Exception as e:
            logger.error(f"Sentiment analysis error: {e}")
            return {
                'score': 0.0,
                'magnitude': 0.0,
                'label': 'neutral'
            }
    
    def _fallback_sentiment(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Simple keyword-based sentiment (fallback)."""
        positive_words = ['up', 'gain', 'high', 'surge', 'bullish', 'growth', 'profit', 'beat']
        negative_words = ['down', 'loss', 'low', 'drop', 'bearish', 'decline', 'miss', 'fall']
        
        pos_count = 0
        neg_count = 0
        
        for article in articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            
            for word in positive_words:
                pos_count += text.count(word)
            
            for word in negative_words:
                neg_count += text.count(word)
        
        total = pos_count + neg_count
        if total == 0:
            score = 0.0
        else:
            score = (pos_count - neg_count) / total
        
        magnitude = abs(score)
        
        if score > 0.2:
            label = 'positive'
        elif score < -0.2:
            label = 'negative'
        else:
            label = 'neutral'
        
        return {
            'score': float(score),
            'magnitude': float(magnitude),
            'label': label
        }


def main():
    """Run the Sentiment Analysis Agent."""
    from core.utils import setup_logging
    setup_logging()
    
    agent = SentimentAgent()
    agent.run()


if __name__ == "__main__":
    main()
