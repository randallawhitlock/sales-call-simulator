"""Voice calls: a LiveKit agent worker plays the customer on Gemini Live.

``config`` and ``ledger`` are plain Python so the API and the tests load them without the agent
framework. ``worker`` is the LiveKit process and the only module that imports it.
"""
