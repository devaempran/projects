# Stock Market Intelligence Platform

A production-grade, multi-agent system for real-time stock market intelligence focused on the opening 10 minutes of trading.

## 🎯 Overview

This platform continuously ingests stock market data, processes it through autonomous agents, and outputs trading signals, risk assessments, and alerts optimized for early market opportunities.

## 🏗️ Architecture

- **Event-Driven**: Redis Streams for agent communication
- **Multi-Agent**: 9 specialized agents working autonomously
- **Time-Series Storage**: PostgreSQL + TimescaleDB
- **Real-Time Dashboard**: Streamlit interface

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- API Keys:
  - Polygon.io (primary)
  - Alpha Vantage (backup)
  - News API (optional)

### Installation

```bash
# Clone and setup
cd Finance
python -m venv venv
.\venv\Scripts\activate  # Windows
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start infrastructure
docker-compose up -d

# Initialize database
python scripts/init_db.py

# Run agents
python main.py

# Launch dashboard (separate terminal)
streamlit run dashboard/app.py
```

## 📊 Trading Strategy

**Focus: Opening 10 Minutes (9:30 AM - 9:40 AM EST)**

- Monitors high-volume stocks during market open
- Detects momentum, volatility patterns
- Generates signals for quick intraday opportunities
- Implements strict risk management

## 🤖 Agents

1. **Market Data Agent**: Real-time data ingestion
2. **Technical Analysis Agent**: RSI, MACD, BB, MA
3. **Sentiment Agent**: News/sentiment analysis
4. **Anomaly Detection Agent**: Statistical outlier detection
5. **Risk Management Agent**: Position sizing, drawdown control
6. **Debate Agents**: Bull/Bear/Judge consensus
7. **Decision Agent**: Signal fusion and scoring
8. **Execution Agent**: Paper trading simulation
9. **Alert Agent**: Notifications and alerts

## 📁 Project Structure

```
finance-platform/
├── agents/          # Agent implementations
├── core/            # Event bus, schemas, config
├── data/            # Database layer
├── dashboard/       # Streamlit UI
├── scripts/         # Utility scripts
├── tests/           # Test suite
└── logs/            # Application logs
```

## 🔧 Configuration

Edit `core/config.py` for:
- Watchlist symbols
- Trading hours (default: 9:30-9:40 AM EST)
- Risk parameters
- Agent thresholds

## 📈 Performance Metrics

- Win Rate
- Sharpe Ratio
- Maximum Drawdown
- Daily PnL
- Signal Accuracy

## 🛠️ Development

```bash
# Run tests
pytest tests/

# Run specific agent
python -m agents.technical_agent

# View logs
tail -f logs/platform.log
```

## 🔐 Security

- Never commit `.env` file
- Store API keys securely
- Use environment variables
- Implement rate limiting

## 📝 License

MIT License

## ⚠️ Disclaimer

This is for educational and paper trading purposes only. Not financial advice.
