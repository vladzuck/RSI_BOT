# Web3 Quant Competition: HK vs SG

This repository contains the algorithmic trading logic, backtesting infrastructure, and live execution scripts developed for the **Web3 Quant Competition (Hong Kong vs. Singapore)**. 

🏆 **Competition Achievement:** Our team achieved the **highest overall composition score** during the first stage of the competition, outperforming peer algorithms in structural logic and execution efficiency.

## ⚙️ Architecture & Components

The system is built entirely in Python, utilizing historical tick data to validate quantitative models before deploying them into live execution pipelines.

* `backtest.py`: Core backtesting engine used to simulate strategy performance, calculate drawdowns, and optimize parameters against historical data.
* `btc5m.csv`: Historical 5-minute Bitcoin OHLCV dataset used for localized modeling and strategy validation.
* `rsi_trading_bot.py`: Live execution module implementing a mean-reversion algorithmic strategy based on Relative Strength Index (RSI) triggers.
* `new_strategy.py` & `vdaw.py`: Experimental modules for testing alternative quantitative signals and market microstructure adaptations.
* `check_account.py`: Account state synchronization script, ensuring safe margin and risk limits before order execution.

## 🚀 Tech Stack
* **Language:** Python
* **Focus:** Algorithmic Trading, Backtesting, Risk Management, Web3 API Integration

## 📌 Usage
*(Note: To run the live execution bots, ensure your API keys are securely stored in your environment variables and never hardcoded.)*

1.  Clone the repository.
2.  Install dependencies from your local virtual environment (`.venv`).
3.  Run `backtest.py` against the `btc5m.csv` data to validate current logic before interacting with the live exchange API.
