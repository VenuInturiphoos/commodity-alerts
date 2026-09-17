import re

with open('price_checker.py', 'r') as f:
    content = f.read()

# 1. Add imports
content = content.replace("import math", "import math\nimport numpy as np\nfrom scipy.signal import argrelextrema")

# 2. Add get_algorithmic_levels before get_support_resistance_levels
method_str = """
    def get_algorithmic_levels(self, yf_symbol, current_price):
        try:
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period="6mo")
            if df.empty:
                return None
                
            order = 5
            local_min_idx = argrelextrema(df['Low'].values, np.less, order=order)[0]
            local_max_idx = argrelextrema(df['High'].values, np.greater, order=order)[0]
            
            support_prices = df['Low'].iloc[local_min_idx].values
            resistance_prices = df['High'].iloc[local_max_idx].values
            
            def cluster_prices(prices, threshold=0.01):
                clusters = []
                for p in sorted(prices):
                    added = False
                    for cluster in clusters:
                        avg = np.mean(cluster)
                        if abs(p - avg) / avg <= threshold:
                            cluster.append(p)
                            added = True
                            break
                    if not added:
                        clusters.append([p])
                results = []
                for c in clusters:
                    results.append({"price": np.mean(c), "strength": len(c)})
                return results
                
            supports = cluster_prices(support_prices)
            resistances = cluster_prices(resistance_prices)
            
            valid_supports = [s for s in supports if s['price'] < current_price]
            valid_resistances = [r for r in resistances if r['price'] > current_price]
            
            valid_supports = sorted(valid_supports, key=lambda x: x['price'], reverse=True)
            valid_resistances = sorted(valid_resistances, key=lambda x: x['price'])
            
            return {
                "Strong_S1": valid_supports[0]['price'] if len(valid_supports) > 0 else None,
                "Strong_S2": valid_supports[1]['price'] if len(valid_supports) > 1 else None,
                "Strong_R1": valid_resistances[0]['price'] if len(valid_resistances) > 0 else None,
                "Strong_R2": valid_resistances[1]['price'] if len(valid_resistances) > 1 else None,
            }
        except Exception as e:
            print(f"Error calculating algorithmic levels for {yf_symbol}: {e}")
            return None

    def get_support_resistance_levels"""

content = content.replace("    def get_support_resistance_levels", method_str)

# 3. Update get_support_resistance_levels to return algorithmic levels for commodities
# Wait, let's just append it to the dictionary returned.
update_levels_str = """
        # 3. If commodity, supplement with strong algorithmic levels
        if is_commodity and yf_symbol and current_price:
            algo_levels = self.get_algorithmic_levels(yf_symbol, current_price)
            if algo_levels:
                if levels is None:
                    levels = {}
                levels.update(algo_levels)
                
        return levels
"""
# Replace the end of the method:
# Find:
#         except Exception as e:
#             print(f"Error fetching YF historical data for {yf_symbol}: {e}")
#             return None
# 
#         return levels
pattern = r"        except Exception as e:\n            print\(f\"Error fetching YF historical data for \{yf_symbol\}: \{e\}\"\)\n            return None\n\n        return levels"
if not re.search(pattern, content):
    print("Could not find the end of get_support_resistance_levels to replace.")
    print("Trying alternative pattern...")
    pattern2 = r"return levels$"
else:
    content = re.sub(pattern, "        except Exception as e:\n            print(f\"Error fetching YF historical data for {yf_symbol}: {e}\")\n\n" + update_levels_str, content)

# 4. Modify evaluate_levels to alert for Strong Support/Resistance for commodities
# The existing high_alerts_config looks like:
#        high_alerts_config = [
#            ('AllTimeHigh', 'All-Time High', 0.01),
# ...
# We need to add Strong_R1, Strong_R2 to high alerts, and Strong_S1, Strong_S2 to low alerts, but only if is_commodity is true.
# Actually, evaluate_levels has no is_commodity param, but we can just check if Strong_S1 is in levels!
alerts_update = """
        high_alerts_config = [
            ('Strong_R2', 'Strong Algorithmic Resistance (R2)', 0.01),
            ('Strong_R1', 'Strong Algorithmic Resistance (R1)', 0.005),
            ('AllTimeHigh', 'All-Time High', 0.01),
            ('OneYearHigh', '1-Year High', 0.01),
            ('ThreeMonthHigh', '3-Month High', 0.01),
            ('TwoMonthHigh', '2-Month High', 0.01),
            ('MonthlyHigh', '1-Month High', 0.005)
        ]
"""
content = content.replace("        high_alerts_config = [\n            ('AllTimeHigh', 'All-Time High', 0.01),", alerts_update)

low_alerts_update = """
        low_alerts_config = [
            ('Strong_S2', 'Strong Algorithmic Support (S2)', 0.01),
            ('Strong_S1', 'Strong Algorithmic Support (S1)', 0.005),
            ('AllTimeLow', 'All-Time Low', 0.01),
"""
content = content.replace("        low_alerts_config = [\n            ('AllTimeLow', 'All-Time Low', 0.01),", low_alerts_update)

# 5. Fix current_price not passed to get_support_resistance_levels!
# In check_alerts:
# levels = self.get_support_resistance_levels(symbol, is_commodity=True, fallback_multiplier=comm['mcx_multiplier'], yf_symbol=comm['yf_symbol'])
# We need to fetch the price FIRST, then pass it to get_support_resistance_levels.
# Let's check how check_alerts works. 
# ... I will just let the patch run and then I'll inspect check_alerts.

with open('price_checker.py', 'w') as f:
    f.write(content)
print("Patch applied.")
