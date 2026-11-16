"""Partial exits for paper and governed demo positions.

Pure, venue-agnostic rules: leg sizing, position exit state, breakeven
protection and the trailing runner. Venue adapters persist the records and
place orders; nothing here reads market data or calls an exchange.
"""
