"""
Sector mappings and indices configuration for the 154 Clean Non-Index F&O Stocks.
"""

SECTORS_META = {
    "IT": {
        "name": "Information Technology",
        "index_ticker": "^CNXIT",
        "icon": "💻",
        "color": "#3b82f6"
    },
    "BANK_FIN": {
        "name": "Banking & Financial Services",
        "index_ticker": "^CNXFIN",
        "icon": "🏦",
        "color": "#10b981"
    },
    "AUTO": {
        "name": "Automobile & Ancillaries",
        "index_ticker": "^CNXAUTO",
        "icon": "🚗",
        "color": "#f59e0b"
    },
    "PHARMA": {
        "name": "Pharma & Healthcare",
        "index_ticker": "^CNXPHARMA",
        "icon": "💊",
        "color": "#ec4899"
    },
    "METAL": {
        "name": "Metals & Mining",
        "index_ticker": "^CNXMETAL",
        "icon": "⛏️",
        "color": "#8b5cf6"
    },
    "ENERGY": {
        "name": "Oil, Gas & Energy",
        "index_ticker": "^CNXENERGY",
        "icon": "⚡",
        "color": "#ef4444"
    },
    "REALTY_INFRA": {
        "name": "Realty & Infrastructure",
        "index_ticker": "^CNXREALTY",
        "icon": "🏗️",
        "color": "#06b6d4"
    },
    "CONSUMER": {
        "name": "FMCG, Retail & Consumption",
        "index_ticker": "^CNXFMCG",
        "icon": "🛒",
        "color": "#84cc16"
    },
    "DEFENSE_ENG": {
        "name": "Defense & Capital Goods",
        "index_ticker": "^CNXPSE",
        "icon": "🛡️",
        "color": "#6366f1"
    },
    "MATERIALS": {
        "name": "Cement, Chemicals & Materials",
        "index_ticker": "^CNXINFRA",
        "icon": "🧱",
        "color": "#14b8a6"
    }
}

STOCK_TO_SECTOR = {
    # IT
    "INFY": "IT", "TCS": "IT", "WIPRO": "IT", "COFORGE": "IT", "PERSISTENT": "IT", 
    "MPHASIS": "IT", "LTIM": "IT", "LTTS": "IT", "OFSS": "IT", "TATAELXSI": "IT", 
    "KPITTECH": "IT", "NAUKRI": "IT", "DIXON": "IT", "KAYNES": "IT", "TATATECH": "IT",
    
    # BANKING & FINANCIAL SERVICES
    "AUBANK": "BANK_FIN", "BANKINDIA": "BANK_FIN", "CANBK": "BANK_FIN", "IDFCFIRSTB": "BANK_FIN", 
    "INDIANB": "BANK_FIN", "INDUSINDBK": "BANK_FIN", "PNB": "BANK_FIN", "RBLBANK": "BANK_FIN", 
    "UNIONBANK": "BANK_FIN", "YESBANK": "BANK_FIN", "ABCAPITAL": "BANK_FIN", "ANGELONE": "BANK_FIN", 
    "BAJAJHLDNG": "BANK_FIN", "BSE": "BANK_FIN", "CAMS": "BANK_FIN", "CDSL": "BANK_FIN", 
    "CHOLAFIN": "BANK_FIN", "HDFCAMC": "BANK_FIN", "HDFCLIFE": "BANK_FIN", "ICICIGI": "BANK_FIN", 
    "ICICIPRULI": "BANK_FIN", "LICI": "BANK_FIN", "LTF": "BANK_FIN", "MCX": "BANK_FIN", 
    "MFSL": "BANK_FIN", "MOTILALOFS": "BANK_FIN", "MUTHOOTFIN": "BANK_FIN", "NUVAMA": "BANK_FIN", 
    "PAYTM": "BANK_FIN", "PFC": "BANK_FIN", "POLICYBZR": "BANK_FIN", "POONAWALLA": "BANK_FIN", 
    "RECLTD": "BANK_FIN", "SBICARD": "BANK_FIN", "360ONE": "BANK_FIN",
    
    # AUTO
    "ASHOKLEY": "AUTO", "ATHERENERG": "AUTO", "BHARATFORG": "AUTO", "BOSCHLTD": "AUTO", 
    "FORCEMOT": "AUTO", "HEROMOTOCO": "AUTO", "HYUNDAI": "AUTO", "MOTHERSON": "AUTO", 
    "SONACOMS": "AUTO", "TIINDIA": "AUTO", "TVSMOTOR": "AUTO", "UNOMINDA": "AUTO",
    
    # PHARMA & HEALTHCARE
    "ALKEM": "PHARMA", "AUROPHARMA": "PHARMA", "BIOCON": "PHARMA", "DIVISLAB": "PHARMA", 
    "GLENMARK": "PHARMA", "GRANULES": "PHARMA", "IPCALAB": "PHARMA", "LAURUSLABS": "PHARMA", 
    "LUPIN": "PHARMA", "MANKIND": "PHARMA", "SUNPHARMA": "PHARMA", "TORNTPHARM": "PHARMA", 
    "ZYDUSLIFE": "PHARMA", "APOLLOHOSP": "PHARMA", "FORTIS": "PHARMA", "LALPATHLAB": "PHARMA", 
    "MAXHEALTH": "PHARMA", "METROPOLIS": "PHARMA",
    
    # METALS
    "HINDALCO": "METAL", "JINDALSTEL": "METAL", "JSWSTEEL": "METAL", "NATIONALUM": "METAL", 
    "NMDC": "METAL", "SAIL": "METAL", "VEDL": "METAL",
    
    # OIL, GAS & ENERGY
    "ADANIENSOL": "ENERGY", "ADANIGREEN": "ENERGY", "ADANIPOWER": "ENERGY", "GAIL": "ENERGY", 
    "HINDPETRO": "ENERGY", "IOC": "ENERGY", "IREDA": "ENERGY", "IRFC": "ENERGY", 
    "JSWENERGY": "ENERGY", "NHPC": "ENERGY", "NTPC": "ENERGY", "OIL": "ENERGY", 
    "ONGC": "ENERGY", "PETRONET": "ENERGY", "POWERGRID": "ENERGY", "POWERINDIA": "ENERGY", 
    "SUZLON": "ENERGY", "TATAPOWER": "ENERGY", "TORNTPOWER": "ENERGY",
    
    # REALTY & INFRASTRUCTURE
    "DLF": "REALTY_INFRA", "GODREJPROP": "REALTY_INFRA", "LODHA": "REALTY_INFRA", 
    "OBEROIRLTY": "REALTY_INFRA", "PHOENIXLTD": "REALTY_INFRA", "PRESTIGE": "REALTY_INFRA", 
    "GMRAIRPORT": "REALTY_INFRA", "NBCC": "REALTY_INFRA", "BHEL": "REALTY_INFRA", 
    "CGPOWER": "REALTY_INFRA", "CUMMINSIND": "REALTY_INFRA", "KEI": "REALTY_INFRA", 
    "POLYCAB": "REALTY_INFRA", "SIEMENS": "REALTY_INFRA", "ABB": "REALTY_INFRA", 
    "AMBER": "REALTY_INFRA", "VOLTAS": "REALTY_INFRA", "HAVELLS": "REALTY_INFRA", 
    "CROMPTON": "REALTY_INFRA", "BLUESTARCO": "REALTY_INFRA",
    
    # CONSUMER, FMCG & RETAIL
    "COLPAL": "CONSUMER", "DABUR": "CONSUMER", "GODFRYPHLP": "CONSUMER", "GODREJCP": "CONSUMER", 
    "JUBLFOOD": "CONSUMER", "MARICO": "CONSUMER", "NESTLEIND": "CONSUMER", "PATANJALI": "CONSUMER", 
    "VBL": "CONSUMER", "DELHIVERY": "CONSUMER", "DMART": "CONSUMER", "ETERNAL": "CONSUMER", 
    "KALYANKJIL": "CONSUMER", "NYKAA": "CONSUMER", "PAGEIND": "CONSUMER", "SWIGGY": "CONSUMER", 
    "TRENT": "CONSUMER", "IDEA": "CONSUMER", "INDUSTOWER": "CONSUMER", "TATACOMM": "CONSUMER", 
    "INDIAMART": "CONSUMER",
    
    # DEFENSE & CAPITAL GOODS
    "BDL": "DEFENSE_ENG", "COCHINSHIP": "DEFENSE_ENG", "HAL": "DEFENSE_ENG", 
    "MAZDOCK": "DEFENSE_ENG", "RVNL": "DEFENSE_ENG", "SOLARINDS": "DEFENSE_ENG", 
    "TITAGARH": "DEFENSE_ENG",
    
    # MATERIALS & CEMENT
    "AMBUJACEM": "MATERIALS", "APLAPOLLO": "MATERIALS", "ASTRAL": "MATERIALS", 
    "SHREECEM": "MATERIALS", "SUPREMEIND": "MATERIALS", "ULTRACEMCO": "MATERIALS", 
    "PIDILITIND": "MATERIALS", "SRF": "MATERIALS", "SCHAEFFLER": "MATERIALS", "UPL": "MATERIALS"
}

def get_sector_for_stock(symbol: str) -> str:
    return STOCK_TO_SECTOR.get(symbol.upper(), "CONSUMER")
