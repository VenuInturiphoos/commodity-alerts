"""
Dhan Brokerage & Regulatory Charges Calculator (India)
======================================================
Accurately models all brokerage and statutory government taxes applicable
for trades placed via Dhan (Raise Financial Services / DhanHQ).

Covers all 4 core asset classes in the trading engine:
1. MCX Commodity Futures (Crude Oil, Natural Gas, Silver, Gold, Copper, Zinc, Aluminium)
2. NSE Equity Futures (F&O Stock Futures)
3. NSE Equity Intraday (MIS 5x Margin Trading)
4. NSE Equity Delivery (CNC Long-term Investments)

Tariff Rates & Statutory Taxes (SEBI / Finance Act Tariff Schedule):
- Brokerage: Flat ₹20 per executed order or 0.03% (whichever is lower). ₹0 for Delivery.
- STT / CTT:
  - MCX Futures: 0.01% CTT on SELL turnover only (Non-agri commodities)
  - NSE Equity Futures: 0.02% STT on SELL turnover only (Budget / SEBI revised rate)
  - NSE Equity Intraday: 0.025% STT on SELL turnover only
  - NSE Equity Delivery: 0.1% STT on BUY & SELL turnover
- Exchange Transaction Charges:
  - MCX: 0.0021% on total turnover
  - NSE Futures: 0.00173% on total turnover
  - NSE Equity (MIS & CNC): 0.00297% on total turnover
- SEBI Turnover Charges: ₹10 / crore (0.000001) on total turnover
- Stamp Duty (Uniform National Stamp Act):
  - MCX Futures: 0.002% on BUY turnover only
  - NSE Equity Futures: 0.002% on BUY turnover only
  - NSE Equity Intraday: 0.003% on BUY turnover only
  - NSE Equity Delivery: 0.015% on BUY turnover only
- GST: 18% on (Brokerage + Exchange Charges + SEBI Turnover Charges)
- DP Charges: ₹12.50 + 18% GST (₹14.75) on Equity Delivery Sell side only
"""

from typing import Dict, Any, Optional

MCX_COMMODITIES = {
    'CRUDEOIL', 'CRUDEOILM', 'NATURALGAS', 'NATURALGASM',
    'SILVER', 'SILVERM', 'SILVERMIC',
    'GOLD', 'GOLDM', 'GOLDTEN',
    'COPPER', 'COPPERM', 'ZINC', 'ZINCM', 'ZINCMINI',
    'ALUMINIUM', 'ALUMINI'
}


class DhanBrokerageCalculator:
    """
    Computes precise Dhan brokerage and all statutory taxes for any executed or hypothetical trade.
    """

    @staticmethod
    def resolve_segment(symbol: str, asset_type: Optional[str] = None, product_type: Optional[str] = None) -> str:
        sym_clean = symbol.replace('-FUT', '').replace('.NS', '').replace('.BO', '').upper()
        a_type = (asset_type or '').upper()
        p_type = (product_type or '').upper()

        if a_type in ['COMMODITY', 'COMMODITY_FUTURES'] or sym_clean in MCX_COMMODITIES or symbol in MCX_COMMODITIES:
            return 'COMMODITY_FUTURES'
        
        if a_type in ['STOCK_FUTURES', 'EQUITY_FUTURES', 'FUTURES'] or '-FUT' in symbol.upper():
            return 'EQUITY_FUTURES'
        
        if a_type in ['EQUITY_DELIVERY', 'INVESTMENT', 'CNC'] or p_type in ['CNC', 'CNC_DELIVERY', 'DELIVERY']:
            return 'EQUITY_DELIVERY'
            
        if a_type in ['INTRADAY_STOCK', 'EQUITY', 'EQUITY_INTRADAY', 'MIS'] or p_type in ['MIS', 'MIS_INTRADAY']:
            return 'EQUITY_INTRADAY'
            
        # Default fallback based on symbol structure
        if '.NS' in symbol or '.BO' in symbol:
            return 'EQUITY_INTRADAY'
            
        return 'COMMODITY_FUTURES'

    @classmethod
    def calculate_trade_charges(
        cls,
        symbol: str,
        asset_type: Optional[str],
        action: str,
        entry_price: Optional[float],
        exit_price: Optional[float],
        quantity: Optional[float],
        product_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculates all charges and net P&L for a completed trade (Entry + Exit).
        """
        # Guard against zero or unexecuted trades
        if (
            not entry_price or entry_price <= 0 or
            not exit_price or exit_price <= 0 or
            not quantity or quantity <= 0 or
            action not in ['BUY', 'SELL']
        ):
            return {
                'broker': 'Dhan',
                'segment': 'UNKNOWN',
                'buy_turnover': 0.0,
                'sell_turnover': 0.0,
                'total_turnover': 0.0,
                'brokerage': 0.0,
                'buy_brokerage': 0.0,
                'sell_brokerage': 0.0,
                'stt_ctt': 0.0,
                'exchange_charges': 0.0,
                'sebi_charges': 0.0,
                'stamp_duty': 0.0,
                'gst': 0.0,
                'dp_charges': 0.0,
                'total_charges': 0.0,
                'gross_pnl': 0.0,
                'net_pnl': 0.0,
                'summary_text': 'Trade stood aside or unexecuted. No charges applicable.'
            }

        segment = cls.resolve_segment(symbol, asset_type, product_type)
        entry_p = float(entry_price)
        exit_p = float(exit_price)
        qty = float(quantity)

        # In Indian markets:
        # Long Trade (BUY): Entry is Buy, Exit is Sell
        # Short Trade (SELL): Entry is Sell, Exit is Buy
        if action == 'BUY':
            buy_turnover = round(entry_p * qty, 2)
            sell_turnover = round(exit_p * qty, 2)
            gross_pnl = round((exit_p - entry_p) * qty, 2)
        else: # SELL (Short)
            buy_turnover = round(exit_p * qty, 2)
            sell_turnover = round(entry_p * qty, 2)
            gross_pnl = round((entry_p - exit_p) * qty, 2)

        total_turnover = round(buy_turnover + sell_turnover, 2)

        # 1. Brokerage
        if segment == 'EQUITY_DELIVERY':
            buy_brokerage = 0.0
            sell_brokerage = 0.0
        else:
            # Dhan standard: min(₹20, 0.03% of trade turnover) per executed order
            buy_brokerage = min(20.0, round(0.0003 * buy_turnover, 2))
            sell_brokerage = min(20.0, round(0.0003 * sell_turnover, 2))
        brokerage = round(buy_brokerage + sell_brokerage, 2)

        # 2. STT / CTT (Tax on transaction)
        if segment == 'COMMODITY_FUTURES':
            # CTT: 0.01% on sell side only (non-agri commodity futures)
            stt_ctt = round(0.0001 * sell_turnover, 2)
        elif segment == 'EQUITY_FUTURES':
            # STT: 0.02% on sell side only
            stt_ctt = round(0.0002 * sell_turnover, 2)
        elif segment == 'EQUITY_INTRADAY':
            # STT: 0.025% on sell side only
            stt_ctt = round(0.00025 * sell_turnover, 2)
        elif segment == 'EQUITY_DELIVERY':
            # STT: 0.1% on buy side AND 0.1% on sell side
            stt_ctt = round(0.001 * buy_turnover + 0.001 * sell_turnover, 2)
        else:
            stt_ctt = 0.0

        # 3. Exchange Turnover Charges
        if segment == 'COMMODITY_FUTURES':
            # MCX: 0.0021% on total turnover
            exchange_charges = round(0.000021 * total_turnover, 2)
        elif segment == 'EQUITY_FUTURES':
            # NSE Futures: 0.00173% on total turnover
            exchange_charges = round(0.0000173 * total_turnover, 2)
        else:
            # NSE Equity Cash (MIS / CNC): 0.00297% on total turnover
            exchange_charges = round(0.0000297 * total_turnover, 2)

        # 4. SEBI Turnover Charges: ₹10 per crore (0.000001) across all segments
        sebi_charges = round(0.000001 * total_turnover, 2)

        # 5. Stamp Duty: Charged on BUY turnover only
        if segment == 'COMMODITY_FUTURES':
            stamp_duty = round(0.00002 * buy_turnover, 2) # 0.002%
        elif segment == 'EQUITY_FUTURES':
            stamp_duty = round(0.00002 * buy_turnover, 2) # 0.002%
        elif segment == 'EQUITY_INTRADAY':
            stamp_duty = round(0.00003 * buy_turnover, 2) # 0.003%
        elif segment == 'EQUITY_DELIVERY':
            stamp_duty = round(0.00015 * buy_turnover, 2) # 0.015%
        else:
            stamp_duty = round(0.00002 * buy_turnover, 2)

        # 6. GST: 18% on (Brokerage + Exchange Charges + SEBI Charges)
        taxable_services = brokerage + exchange_charges + sebi_charges
        gst = round(0.18 * taxable_services, 2)

        # 7. DP Charges (Delivery Sell only): ₹12.50 + 18% GST = ₹14.75 per scrip
        dp_charges = 14.75 if segment == 'EQUITY_DELIVERY' else 0.0

        # 8. Total Charges
        total_charges = round(brokerage + stt_ctt + exchange_charges + sebi_charges + stamp_duty + gst + dp_charges, 2)

        # 9. Net P&L
        net_pnl = round(gross_pnl - total_charges, 2)

        sign_gross = '+' if gross_pnl >= 0 else ''
        sign_net = '+' if net_pnl >= 0 else ''
        summary_text = (
            f"Gross: {sign_gross}₹{gross_pnl:,.2f} | "
            f"Dhan Charges: -₹{total_charges:,.2f} (Brok ₹{brokerage:,.2f} + Taxes ₹{total_charges - brokerage:,.2f}) | "
            f"Net: {sign_net}₹{net_pnl:,.2f}"
        )

        return {
            'broker': 'Dhan',
            'segment': segment,
            'buy_turnover': buy_turnover,
            'sell_turnover': sell_turnover,
            'total_turnover': total_turnover,
            'brokerage': brokerage,
            'buy_brokerage': buy_brokerage,
            'sell_brokerage': sell_brokerage,
            'stt_ctt': stt_ctt,
            'exchange_charges': exchange_charges,
            'sebi_charges': sebi_charges,
            'stamp_duty': stamp_duty,
            'gst': gst,
            'dp_charges': dp_charges,
            'total_charges': total_charges,
            'gross_pnl': gross_pnl,
            'net_pnl': net_pnl,
            'summary_text': summary_text
        }

    @classmethod
    def estimate_open_position_charges(
        cls,
        symbol: str,
        asset_type: Optional[str],
        action: str,
        entry_price: float,
        current_price: float,
        quantity: float,
        product_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Estimates full round-trip Dhan charges and live net MTM assuming position were closed at current_price.
        """
        return cls.calculate_trade_charges(
            symbol=symbol,
            asset_type=asset_type,
            action=action,
            entry_price=entry_price,
            exit_price=current_price,
            quantity=quantity,
            product_type=product_type
        )
