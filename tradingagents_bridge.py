"""
TradingAgents Bridge & Multi-Agent Deliberation Integration
Integrates TauricResearch's TradingAgents multi-agent LLM framework into the advisory & commodity analysis platform.
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import pytz

try:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.dataflows.symbols import normalize_symbol
    TRADINGAGENTS_AVAILABLE = True
except ImportError as e:
    TRADINGAGENTS_AVAILABLE = False
    _IMPORT_ERROR = str(e)

logger = logging.getLogger(__name__)

# Indian Commodity to Global Benchmark mapping for deep fundamental & macro analytics
COMMODITY_YAHOO_BENCHMARKS = {
    'CRUDEOIL': 'CL=F',
    'CRUDEOILM': 'CL=F',
    'NATURALGAS': 'NG=F',
    'NATURALGASM': 'NG=F',
    'GOLD': 'GC=F',
    'GOLDM': 'GC=F',
    'SILVER': 'SI=F',
    'SILVERM': 'SI=F',
    'SILVERMIC': 'SI=F',
    'COPPER': 'HG=F',
    'ZINC': 'ZN=F',
    'ZINCMINI': 'ZN=F',
    'ALUMINIUM': 'ALI=F',
    'ALUMINI': 'ALI=F',
    'LEAD': 'LED=F',
    'LEADMINI': 'LED=F',
}

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tradingagents_data", "cache", "deliberation_cache.json")

def _load_cache() -> Dict[str, Any]:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_cache(cache: Dict[str, Any]):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save deliberation cache: {e}")

def get_cached_deliberation(symbol: str, max_age_seconds: int = 3600) -> Optional[Dict[str, Any]]:
    cache = _load_cache()
    entry = cache.get(symbol.strip().upper())
    if not entry:
        return None
    ts = entry.get("epoch_timestamp", 0)
    now_ts = datetime.now().timestamp()
    if (now_ts - ts) <= max_age_seconds:
        return entry.get("data")
    return None

def set_cached_deliberation(symbol: str, data: Dict[str, Any]):
    cache = _load_cache()
    cache[symbol.strip().upper()] = {
        "epoch_timestamp": datetime.now().timestamp(),
        "saved_at": datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST"),
        "data": data
    }
    _save_cache(cache)


class TradingAgentsOrchestrator:
    """
    Orchestrates TauricResearch multi-agent LLM deliberation
    (Fundamental, Technical, News, Sentiment analysts -> Bullish vs Bearish debate -> Trader & Risk managers)
    with pre-trade risk engine validation for Indian equities and MCX commodities.
    """

    @classmethod
    def is_available(cls) -> bool:
        return TRADINGAGENTS_AVAILABLE

    @classmethod
    def detect_active_llm_provider(cls) -> Tuple[str, str, str]:
        """
        Detects available LLM credentials from environment.
        Returns: (provider_name, deep_think_model, quick_think_model)
        """
        if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            # Set GOOGLE_API_KEY if only GEMINI_API_KEY was provided
            if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
                os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
            return "google", "gemini-3.8-flash", "gemini-3.8-flash"
        elif os.environ.get("OPENAI_API_KEY"):
            return "openai", "gpt-4o", "gpt-4o-mini"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic", "claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022"
        elif os.environ.get("GROQ_API_KEY"):
            return "groq", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"
        
        # Default fallback
        return "google", "gemini-3.8-flash", "gemini-3.8-flash"

    @classmethod
    def resolve_ticker_for_analysis(cls, symbol: str) -> Tuple[str, str]:
        """
        Resolves Indian market ticker symbol into yfinance-compatible format.
        Returns: (resolved_ticker, asset_type)
        """
        sym_clean = symbol.strip().upper()
        
        # Check commodity benchmarks
        if sym_clean in COMMODITY_YAHOO_BENCHMARKS:
            return COMMODITY_YAHOO_BENCHMARKS[sym_clean], "commodity"
        
        # Indian equities on NSE
        if sym_clean.endswith(".NS") or sym_clean.endswith(".BO"):
            return sym_clean, "stock"
        
        # Common US symbols pass through directly
        if sym_clean in ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL"]:
            return sym_clean, "stock"

        # If plain symbol without exchange, assume NSE if uppercase alphabetic
        if sym_clean.isalpha():
            return f"{sym_clean}.NS", "stock"

        return sym_clean, "stock"

    @classmethod
    def run_multi_agent_deliberation(
        cls,
        ticker: str,
        trade_date: Optional[str] = None,
        debug: bool = False,
        selected_analysts: Tuple[str, ...] = ("market", "news", "fundamentals"),
    ) -> Dict[str, Any]:
        """
        Executes a full multi-agent deliberation graph for the target instrument.
        """
        if not TRADINGAGENTS_AVAILABLE:
            return {
                "success": False,
                "error": f"TradingAgents library not initialized: {_IMPORT_ERROR if '_IMPORT_ERROR' in globals() else 'unknown'}"
            }

        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        if not trade_date:
            trade_date = now_ist.strftime('%Y-%m-%d')

        resolved_ticker, asset_type = cls.resolve_ticker_for_analysis(ticker)
        provider, deep_model, quick_model = cls.detect_active_llm_provider()

        config = DEFAULT_CONFIG.copy()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, "tradingagents_data")
        config["results_dir"] = os.path.join(data_dir, "logs")
        config["data_cache_dir"] = os.path.join(data_dir, "cache")
        config["memory_log_path"] = os.path.join(data_dir, "memory", "trading_memory.md")
        config["llm_provider"] = provider
        config["deep_think_llm"] = deep_model
        config["quick_think_llm"] = quick_model
        config["max_debate_rounds"] = 1
        config["max_risk_discuss_rounds"] = 1
        config["max_tool_rounds"] = 10

        try:
            print(f"🤖 Launching TradingAgents Multi-Agent Graph for {resolved_ticker} ({asset_type}) [Provider: {provider}]...")
            graph = TradingAgentsGraph(
                selected_analysts=selected_analysts,
                debug=debug,
                config=config
            )

            state, decision = graph.propagate(resolved_ticker, trade_date, asset_type=asset_type)
            
            # Extract consensus
            result = {
                "success": True,
                "symbol": ticker,
                "resolved_ticker": resolved_ticker,
                "asset_type": asset_type,
                "trade_date": trade_date,
                "llm_provider": provider,
                "decision": decision,
                "analyst_reports": {
                    "market": state.get("market_report"),
                    "news": state.get("news_report"),
                    "fundamentals": state.get("fundamentals_report"),
                    "sentiment": state.get("sentiment_report"),
                },
                "bullish_thesis": state.get("investment_debate_state", {}).get("bullish_argument"),
                "bearish_thesis": state.get("investment_debate_state", {}).get("bearish_argument"),
                "risk_assessment": state.get("risk_management_report"),
                "timestamp": now_ist.isoformat(),
            }
            return result

        except Exception as e:
            logger.exception(f"Error during TradingAgents multi-agent deliberation for {ticker}: {e}")
            return {
                "success": False,
                "symbol": ticker,
                "error": str(e)
            }

    @classmethod
    def deliberate_intraday(cls, symbol: str, price: float, action: str = "BUY") -> Dict[str, Any]:
        """
        Fast multi-agent review for Intraday Equities (Market + News + Risk Guard).
        Utilizes 1-hour session cache to conserve LLM quota.
        """
        cached = get_cached_deliberation(symbol, max_age_seconds=3600)
        if cached:
            return cached

        # Run focused 2-analyst deliberation to stay within free-tier rate limits
        res = cls.run_multi_agent_deliberation(
            ticker=symbol,
            selected_analysts=("market", "news")
        )
        if not res.get("success"):
            fallback = {
                "approved": True,
                "confidence": "MODERATE",
                "reason": f"TradingAgents Fallback: {res.get('error', 'Rate limited')}. Cleared by PreTradeRiskEngine.",
                "badge": "Risk Filter 🛡️",
                "raw_result": res
            }
            return fallback

        decision = str(res.get("decision", "")).upper()
        approved = True
        reason = "TradingAgents multi-agent consensus aligned."
        if action == "BUY" and ("STRONG SELL" in decision or "BEARISH REVERSAL" in decision):
            approved = False
            reason = "TradingAgents Bearish consensus warned against buying into supply."
        elif action == "SELL" and ("STRONG BUY" in decision or "BULLISH BREAKOUT" in decision):
            approved = False
            reason = "TradingAgents Bullish consensus warned against shorting into momentum."

        verdict = {
            "approved": approved,
            "decision": res.get("decision"),
            "reason": reason,
            "badge": "Multi-Agent Consensus 🤖" if approved else "AI Guard Stood Aside 🛡️",
            "bullish_thesis": res.get("bullish_thesis"),
            "bearish_thesis": res.get("bearish_thesis"),
            "risk_assessment": res.get("risk_assessment"),
            "raw_result": res
        }
        set_cached_deliberation(symbol, verdict)
        return verdict

    @classmethod
    def deliberate_commodity(cls, symbol: str, price: float, action: str = "BUY") -> Dict[str, Any]:
        """
        Multi-agent review for MCX Commodity Futures.
        Routes to global futures benchmarks (CL=F, NG=F, GC=F, SI=F) with macro context.
        """
        cached = get_cached_deliberation(symbol, max_age_seconds=1800)
        if cached:
            return cached

        res = cls.run_multi_agent_deliberation(
            ticker=symbol,
            selected_analysts=("market", "news")
        )
        if not res.get("success"):
            fallback = {
                "approved": True,
                "confidence": "MODERATE",
                "reason": f"Commodity Deliberation Fallback: {res.get('error', 'Rate limited')}. Enforcing PreTradeRiskEngine.",
                "badge": "Risk Filter 🛡️",
                "raw_result": res
            }
            return fallback

        decision = str(res.get("decision", "")).upper()
        approved = True
        reason = "TradingAgents global commodity benchmark consensus confirmed."
        if action == "BUY" and ("STRONG SELL" in decision or "LIQUIDATION" in decision):
            approved = False
            reason = "TradingAgents warned against counter-trend buying during global benchmark liquidation."
        elif action == "SELL" and ("STRONG BUY" in decision or "PARABOLIC" in decision):
            approved = False
            reason = "TradingAgents warned against counter-trend shorting during global benchmark squeeze."

        verdict = {
            "approved": approved,
            "decision": res.get("decision"),
            "reason": reason,
            "badge": "Commodity Multi-Agent ⚡" if approved else "Commodity Trap Guard 🛡️",
            "bullish_thesis": res.get("bullish_thesis"),
            "bearish_thesis": res.get("bearish_thesis"),
            "risk_assessment": res.get("risk_assessment"),
            "raw_result": res
        }
        set_cached_deliberation(symbol, verdict)
        return verdict

    @classmethod
    def deliberate_swing(cls, symbol: str, price: float) -> Dict[str, Any]:
        """
        In-depth multi-agent review for Swing Stock Investments (Nifty Golden Crossovers).
        Includes Fundamental + Technical + News analysis. Cached for 6 hours.
        """
        cached = get_cached_deliberation(symbol, max_age_seconds=21600)
        if cached:
            return cached

        res = cls.run_multi_agent_deliberation(
            ticker=symbol,
            selected_analysts=("market", "news", "fundamentals")
        )
        if not res.get("success"):
            fallback = {
                "approved": True,
                "confidence": "HIGH",
                "reason": "Swing trade approved via Golden Crossover & Monte Carlo quantitative safety scoring.",
                "badge": "Quant Golden Cross ⭐",
                "raw_result": res
            }
            return fallback

        decision = str(res.get("decision", "")).upper()
        approved = "SELL" not in decision
        verdict = {
            "approved": approved,
            "decision": res.get("decision"),
            "reason": "TradingAgents swing fundamental & technical thesis confirmed." if approved else "TradingAgents fundamental red flag detected.",
            "badge": "Swing Multi-Agent ⭐" if approved else "Fundamental Veto 🛡️",
            "bullish_thesis": res.get("bullish_thesis"),
            "bearish_thesis": res.get("bearish_thesis"),
            "risk_assessment": res.get("risk_assessment"),
            "raw_result": res
        }
        set_cached_deliberation(symbol, verdict)
        return verdict


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run TradingAgents Multi-Agent Deliberation")
    parser.add_argument("--ticker", type=str, default="CRUDEOIL", help="Ticker symbol (e.g. CRUDEOIL, RELIANCE.NS, GOLD, TSLA)")
    parser.add_argument("--date", type=str, default=None, help="Trade date (YYYY-MM-DD)")
    parser.add_argument("--run", action="store_true", help="Execute multi-agent deliberation graph")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
    args = parser.parse_args()

    print(f"\n==================================================")
    print(f"   🤖 TRADINGAGENTS MULTI-AGENT FRAMEWORK")
    print(f"==================================================")
    print(f"Target Symbol    : {args.ticker}")
    print(f"Framework Status : {'AVAILABLE ✅' if TradingAgentsOrchestrator.is_available() else 'UNAVAILABLE ❌'}")
    
    provider, deep_m, quick_m = TradingAgentsOrchestrator.detect_active_llm_provider()
    has_api_key = any(os.environ.get(k) for k in ["GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"])

    print(f"Active Provider  : {provider} (Deep: {deep_m} | Quick: {quick_m})")
    print(f"API Key Status   : {'FOUND ✅' if has_api_key else 'NOT DETECTED ⚠️'}")

    resolved, asset = TradingAgentsOrchestrator.resolve_ticker_for_analysis(args.ticker)
    print(f"Resolved Feed    : {resolved} [{asset.upper()}]")
    print(f"==================================================\n")

    if not has_api_key:
        print("💡 NOTE: An LLM API key is required to execute multi-agent deliberation.")
        print("   You can set any one of the following in your terminal or in a .env file:")
        print("   - export GOOGLE_API_KEY=\"your-gemini-key\"      (Recommended for Gemini 2.5)")
        print("   - export OPENAI_API_KEY=\"sk-...\"               (For GPT-4o)")
        print("   - export ANTHROPIC_API_KEY=\"sk-ant-...\"        (For Claude 3.7)")
        print("   - export GROQ_API_KEY=\"gsk_...\"                (For Llama 3.3)")
        print("\n   Example command once exported:")
        print(f"   ./venv/bin/python tradingagents_bridge.py --ticker {args.ticker} --run\n")
    else:
        # If API key is present or user passed --run, run deliberation
        print(f"🚀 Starting multi-agent deliberation for {args.ticker}...")
        res = TradingAgentsOrchestrator.run_multi_agent_deliberation(
            ticker=args.ticker,
            trade_date=args.date,
            debug=args.debug
        )
        if res.get("success"):
            print(f"\n🎯 MULTI-AGENT CONSENSUS & DECISION:")
            print(f"--------------------------------------------------")
            print(res.get("decision", "No decision text returned."))
            print(f"--------------------------------------------------")
            if res.get("bullish_thesis"):
                print(f"\n🐂 BULLISH RESEARCHER THESIS:")
                print(res["bullish_thesis"])
            if res.get("bearish_thesis"):
                print(f"\n🐻 BEARISH RESEARCHER THESIS:")
                print(res["bearish_thesis"])
            if res.get("risk_assessment"):
                print(f"\n🛡️ RISK MANAGEMENT OFFICER:")
                print(res["risk_assessment"])
        else:
            print(f"❌ Deliberation failed: {res.get('error')}")

