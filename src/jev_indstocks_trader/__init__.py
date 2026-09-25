"""Jev AI + INDstocks automated trading system.

Three-tier architecture:
  1. Data ingestion / feature prep (feature_prep.py)
  2. Jev AI conviction scoring (jev_client.py)
  3. Risk governor + INDstocks execution gateway (risk_governor.py, execution_gateway.py)

See docs/ARCHITECTURE.md for the full design doc.
"""

__version__ = "0.1.0"
