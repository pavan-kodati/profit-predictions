"""Plain-language definitions, shown wherever a term appears (plan v2, section 3). One source for the whole app."""

from __future__ import annotations

GLOSSARY: dict[str, tuple[str, str]] = {
    "share": ("Share", "A small piece of ownership in a company. If the company does well, the share usually becomes worth more."),
    "nse": ("NSE", "The National Stock Exchange of India, where shares are bought and sold. Companies must report important news to it."),
    "nifty": ("Nifty 50", "A list of India's 50 biggest companies. Its value going up or down shows how the market as a whole is doing."),
    "rating": ("Rating", "Our overall score from 1 to 5 for how strong the case is for the price rising in the next few weeks. 5 is strongest."),
    "label": ("Label", "Whether a rise is backed by the company's real results, partly by excitement, or mostly by excitement."),
    "filing": ("Official filing", "A document a company must send to the stock exchange, such as its results or a big new order. False filings are punished, so they're the most trustworthy source."),
    "trigger": ("Trigger", "The specific recent news that could push the price up, for example a big new order or strong results."),
    "verified": ("Verified", "Confirmed by an official filing, or reported by two independent newspapers. Single-source news only counts half."),
    "stop": ("Safety level", "A price we'd sell at if the share falls, to stop a small loss becoming a big one. Also called a stop-loss."),
    "window": ("Holding window", "How long to hold the share at most. After this, the reason for buying has usually played out."),
    "promoter": ("Promoters", "The founders or controlling owners of a company. When they buy more of their own shares, it's usually a good sign; when they sell, it's a warning."),
    "pledge": ("Pledged shares", "Shares the owners have used as security for a loan. If the price falls, lenders may sell them, pushing the price down further."),
    "institution": ("Institutions", "Big professional investors such as mutual funds and insurance companies. Their buying shows confidence."),
    "volume": ("Volume", "How many shares changed hands in a day. Much higher volume than usual means people are paying attention."),
    "delivery": ("Delivery %", "The share of traded shares that buyers actually kept, rather than buying and selling the same day. Higher means real investors, not quick traders."),
    "high52": ("52-week high", "The highest price in the last year. A share near it is in demand."),
    "average": ("Average price (50-day, 200-day)", "The average closing price over that many days. A price above it means the trend is up."),
    "pe": ("Price / yearly profit (P/E)", "The share price divided by the profit per share for one year. A high number means investors already expect a lot."),
    "margin": ("Profit margin", "How much of each rupee of sales the company keeps as profit."),
    "debt": ("Debt / own capital", "How much the company has borrowed compared with its own money. Above 1.5 is a lot of debt."),
    "roe": ("Return on equity", "How much profit the company makes for each rupee its owners have put in. Above 18% is strong."),
    "orderbook": ("Order book", "Work a company has been hired for but hasn't delivered yet. A big order book means sales are lined up."),
    "surveillance": ("Exchange watch list (ASM/GSM)", "Shares the exchange watches for unusual speculation. Extra trading rules apply and they can become hard to sell."),
    "circuit": ("Upper limit (circuit)", "The most a share's price may rise in one day. A share stuck at it may have no sellers, so you can't buy it."),
    "tax": ("Short-term gains tax", "Tax on profit from shares held under a year: 20% of the profit. Held over a year, it's 12.5%."),
    "charges": ("Charges", "Brokerage, government taxes and exchange fees paid on every buy and sell. Around 0.25% of the trade in total."),
    "fd": ("Fixed deposit", "Money left in a bank for a fixed time at a guaranteed interest rate (we assume 7% a year). A safe comparison for any investment."),
    "marketday": ("Market day", "A weekday the stock market is open. Weekends and market holidays don't count."),
    "practice": ("Practice portfolio", "Pretend trades on every pick using real prices, to see if following the app would have made money, before you risk real money."),
}


def lookup(key: str) -> tuple[str, str]:
    return GLOSSARY.get(key, (key, ""))
